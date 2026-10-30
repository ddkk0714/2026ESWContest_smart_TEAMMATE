#pragma once
#include <nlohmann/json.hpp>
#include <functional>
#include <string>
using Json = nlohmann::json;
struct Credentials {
 std::string pat,clientId,apiKey;
 bool verifiedProvider=false;
 std::string mode="live_read_only";
 std::function<bool()> authorized;
 Credentials()=default;
 Credentials(std::string p,std::string c,std::string k,bool verified)
   :pat(std::move(p)),clientId(std::move(c)),apiKey(std::move(k)),verifiedProvider(verified){}
 Credentials(const Credentials&)=delete;
 Credentials& operator=(const Credentials&)=delete;
 Credentials(Credentials&&)=default;
 Credentials& operator=(Credentials&&)=default;
 ~Credentials();
 bool valid() const {return verifiedProvider && (!authorized || authorized());}
};
struct HttpsRequest { std::string method, path; Json payload; };
using HttpsExecutor = std::function<Json(const HttpsRequest&, const Credentials&)>;
Json curlRequest(const HttpsRequest&, const Credentials&);
class GuardedHttps {
  Credentials credentials_;
  HttpsExecutor execute_;
  std::string selected_, permit_;
public:
  GuardedHttps(Credentials credentials, HttpsExecutor executor = curlRequest)
      : credentials_(std::move(credentials)), execute_(std::move(executor)) {}
  void approve(const std::string& value) { permit_ = value; }
  Json request(const HttpsRequest&);
};
