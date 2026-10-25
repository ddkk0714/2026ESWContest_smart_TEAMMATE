#include "https.h"
#include <curl/curl.h>
#include <openssl/rand.h>
#include <openssl/evp.h>
#include <algorithm>
#include <cctype>
#include <regex>
#include <memory>
namespace {
Json denied(const char* reason) { return {{"error",reason}}; }
bool safeHeader(const std::string& v) {
 return !v.empty() && v.size() < 4096 && std::none_of(v.begin(),v.end(),[](unsigned char c){return std::iscntrl(c) || std::isspace(c);});
}
size_t append(char* data, size_t size, size_t n, void* dest) {
 auto& out=*static_cast<std::string*>(dest); const auto bytes=size*n;
 if(bytes > 1024*1024 || out.size()+bytes>1024*1024) return 0;
 out.append(data,bytes); return bytes;
}
}
Credentials::~Credentials(){
 for(auto* s:{&pat,&clientId,&apiKey}) {volatile char* p=s->empty()?nullptr:&(*s)[0];for(size_t i=0;i<s->size();i++)p[i]=0;}
}
Json GuardedHttps::request(const HttpsRequest& r) {
 std::string permit;
 if(r.method=="POST") { permit=std::move(permit_);permit_.clear(); }
 if(credentials_.verifiedProvider && credentials_.authorized && !credentials_.authorized())return denied("authorization_expired_or_revoked");
 if(!credentials_.valid() || !safeHeader(credentials_.pat) ||
    !safeHeader(credentials_.clientId) || !safeHeader(credentials_.apiKey)) return denied("credentials_provider_unverified");
 std::smatch match;
 const bool list=r.path=="/devices";
 if(!list && !std::regex_match(r.path,match,std::regex("^/devices/([A-Za-z0-9_-]+)/(profile|state|control)$"))) return denied("endpoint_blocked");
 if(!list) {
  if(selected_.empty() && r.method=="GET" && match[2]=="profile") selected_=match[1];
  if(selected_.empty() || selected_!=match[1]) return denied("target_changed");
 }
 if(r.method=="GET") {
  if(credentials_.mode!="live_read_only" && credentials_.mode!="live_manual")return denied("mode_blocked");
  if(!list && match[2]=="control") return denied("method_blocked");
  if(!r.payload.is_null()) return denied("get_payload_blocked");
 } else if(r.method=="POST") {
  if(credentials_.mode!="live_manual")return denied("read_only_post_blocked");
  // Permit was consumed before validation and I/O, including rejected attempts.
  if(list || match[2]!="control" || (permit!="LOW" && permit!="AUTO") ||
     r.payload!=Json{{"airFlow",{{"windStrength",permit}}}}) return denied("post_not_approved");
 } else return denied("method_blocked");
 return execute_(r,credentials_); // exactly one call; no retry or automatic cleanup POST
}
Json curlRequest(const HttpsRequest& r,const Credentials& c) {
 if(!c.valid())return denied("authorization_expired_or_revoked");
 auto easy=std::unique_ptr<CURL,decltype(&curl_easy_cleanup)>(curl_easy_init(),curl_easy_cleanup);
 if(!easy) return denied("transport_unavailable");
 unsigned char random[16]; unsigned char encoded[25]{};
 if(RAND_bytes(random,16)!=1) return denied("entropy_unavailable");
 EVP_EncodeBlock(encoded,random,16);
 std::string message(reinterpret_cast<char*>(encoded),22);
 std::replace(message.begin(),message.end(),'+','-'); std::replace(message.begin(),message.end(),'/','_');
 auto freeHeaders=[](curl_slist* h){for(auto* p=h;p;p=p->next){if(p->data){volatile char* b=p->data;for(size_t i=0,n=std::char_traits<char>::length(p->data);i<n;i++)b[i]=0;}}curl_slist_free_all(h);};
 auto headers=std::unique_ptr<curl_slist,decltype(freeHeaders)>(nullptr,freeHeaders);
 auto add=[&](std::string header){
  auto* next=curl_slist_append(headers.get(),header.c_str());
  volatile char* p=header.empty()?nullptr:&header[0];for(size_t i=0;i<header.size();i++)p[i]=0;
  if(!next)throw std::runtime_error("header_allocation_failed");
  (void)headers.release();headers.reset(next);
 };
 // Standard SDK headers only; no cookies or arbitrary caller-provided headers.
 add("Authorization: Bearer "+c.pat);add("x-country: KR");add("x-message-id: "+message);
 add("x-client-id: "+c.clientId);add("x-api-key: "+c.apiKey);add("x-service-phase: OP");add("Content-Type: application/json");
 if(r.method=="POST")add("x-conditional-control: true");
 std::string body; const auto url="https://api-kic.lgthinq.com"+r.path; const auto payload=r.payload.dump();
 curl_easy_setopt(easy.get(),CURLOPT_URL,url.c_str());
 curl_easy_setopt(easy.get(),CURLOPT_HTTPHEADER,headers.get());
 curl_easy_setopt(easy.get(),CURLOPT_PROXY,""); // never inherit credential-leaking ambient proxies
 curl_easy_setopt(easy.get(),CURLOPT_PROTOCOLS_STR,"https");
 curl_easy_setopt(easy.get(),CURLOPT_REDIR_PROTOCOLS_STR,"https");
 curl_easy_setopt(easy.get(),CURLOPT_FOLLOWLOCATION,0L);
 curl_easy_setopt(easy.get(),CURLOPT_HTTP_VERSION,CURL_HTTP_VERSION_1_1);
 curl_easy_setopt(easy.get(),CURLOPT_FRESH_CONNECT,1L);
 curl_easy_setopt(easy.get(),CURLOPT_FORBID_REUSE,1L);
 curl_easy_setopt(easy.get(),CURLOPT_SSL_VERIFYPEER,1L);
 curl_easy_setopt(easy.get(),CURLOPT_SSL_VERIFYHOST,2L);
 curl_easy_setopt(easy.get(),CURLOPT_CONNECTTIMEOUT_MS,5000L);
 curl_easy_setopt(easy.get(),CURLOPT_TIMEOUT_MS,10000L);
 curl_easy_setopt(easy.get(),CURLOPT_NOSIGNAL,1L);
 curl_easy_setopt(easy.get(),CURLOPT_NOPROGRESS,0L);
 curl_easy_setopt(easy.get(),CURLOPT_XFERINFODATA,&c);
 curl_easy_setopt(easy.get(),CURLOPT_XFERINFOFUNCTION,+[](void* v,curl_off_t,curl_off_t,curl_off_t,curl_off_t)->int{
  return static_cast<const Credentials*>(v)->valid()?0:1;
 });
 curl_easy_setopt(easy.get(),CURLOPT_WRITEFUNCTION,append);
 curl_easy_setopt(easy.get(),CURLOPT_WRITEDATA,&body);
 if(r.method=="POST") {curl_easy_setopt(easy.get(),CURLOPT_POST,1L);curl_easy_setopt(easy.get(),CURLOPT_POSTFIELDS,payload.c_str());}
 if(!c.valid())return denied("authorization_expired_or_revoked");
 const auto code=curl_easy_perform(easy.get());
 if(code==CURLE_ABORTED_BY_CALLBACK || !c.valid())return denied("authorization_expired_or_revoked");
 if(code!=CURLE_OK) return denied(code==CURLE_OPERATION_TIMEDOUT?"network_timeout":"network_or_tls_error");
 long status=0;curl_easy_getinfo(easy.get(),CURLINFO_RESPONSE_CODE,&status);
 auto data=Json::parse(body,nullptr,false);
 if(status<200 || status>=300) {
  Json out={{"error","api_error"},{"http_status",status}};
  if(data.is_object() && data.contains("error") && data["error"].is_object()) {
   const auto v=data["error"].value("code",Json());
   if(v.is_string() && std::regex_match(v.get<std::string>(),std::regex("^[0-9]{4}$"))) out["code"]=v;
  }
  return out;
 }
 if(data.is_discarded() || !data.is_object() || !data.contains("response")) return denied("invalid_response");
 return {{"response",data["response"]},{"http_status",status}};
}
