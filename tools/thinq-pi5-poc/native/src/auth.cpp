#include "auth.h"
#include <openssl/hmac.h>
#include <openssl/crypto.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <poll.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <array>
#include <algorithm>
#include <arpa/inet.h>

void wipe(void* p,size_t n) noexcept {volatile unsigned char* v=static_cast<volatile unsigned char*>(p);while(n--)*v++=0;}
bool hardenProcess() noexcept {
 rlimit r{0,0};return setrlimit(RLIMIT_CORE,&r)==0 && prctl(PR_SET_DUMPABLE,0)==0;
}
SecretPages::SecretPages(size_t n) {
 const auto page=static_cast<size_t>(sysconf(_SC_PAGESIZE));size_=((n+page-1)/page)*page;
 data_=static_cast<char*>(mmap(nullptr,size_,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0));
 if(data_==MAP_FAILED){data_=nullptr;throw std::runtime_error("secret_memory_unavailable");}
 if(mlock(data_,size_) || madvise(data_,size_,MADV_DONTDUMP) || madvise(data_,size_,MADV_DONTFORK)) {
  wipe(data_,size_);munlock(data_,size_);munmap(data_,size_);data_=nullptr;throw std::runtime_error("secret_memory_protection_failed");
 }
}
SecretPages::~SecretPages(){if(data_){clear();munlock(data_,size_);munmap(data_,size_);}}
void SecretPages::clear() noexcept {if(data_)wipe(data_,size_);}
namespace {
std::string randomId() {
 unsigned char b[16];size_t n=0;
 while(n<sizeof(b)){const auto r=getrandom(b+n,sizeof(b)-n,0);if(r>0)n+=static_cast<size_t>(r);else if(errno!=EINTR)throw std::runtime_error("entropy_unavailable");}
 const char* alphabet="0123456789abcdef";std::string s;for(auto v:b){s+=alphabet[v>>4];s+=alphabet[v&15];}wipe(b,sizeof(b));return s;
}
Json err(const char* s){return {{"status","blocked"},{"error",s}};}
bool principal(const RoleBindings& b,uid_t u){return b.verified && u!=0 && u==b.operatorUid && u!=b.ui;}
bool header(const char* p,size_t n){if(!n)return false;for(size_t i=0;i<n;i++)if(static_cast<unsigned char>(p[i])<=32 || static_cast<unsigned char>(p[i])>=127)return false;return true;}
sockaddr_un address(const std::string& name,socklen_t& len){
 sockaddr_un a{};a.sun_family=AF_UNIX;if(name.empty() || name.size()>sizeof(a.sun_path)-2)throw std::runtime_error("bad_endpoint");
 memcpy(a.sun_path+1,name.data(),name.size());len=static_cast<socklen_t>(offsetof(sockaddr_un,sun_path)+1+name.size());return a;
}
bool peer(int fd,ucred& c){socklen_t n=sizeof(c);return getsockopt(fd,SOL_SOCKET,SO_PEERCRED,&c,&n)==0 && n==sizeof(c);}
}
bool Lease::valid() const noexcept {return !cancelled.load() && Mono::now()<until;}
void Lease::revoke() noexcept {cancelled=true;std::lock_guard<std::mutex> lock(mutex);if(packet)packet->clear();supplied=false;}
Credentials Lease::credentials(){
 std::lock_guard<std::mutex> lock(mutex);
 if(!valid() || !supplied || !packet)return {};
 Credentials c{std::string(packet->data()+offsets[0],lengths[0]),std::string(packet->data()+offsets[1],lengths[1]),std::string(packet->data()+offsets[2],lengths[2]),true};
 c.mode=mode;return c;
}
AuthStore::AuthStore(RoleBindings b):bindings_(b){
 if(!b.verified)return;
 digestKey_=std::make_unique<SecretPages>(32);
 if(getrandom(digestKey_->data(),32,0)!=32)throw std::runtime_error("entropy_unavailable");
}
AuthStore::~AuthStore(){revoke();}
std::string AuthStore::fingerprint(const char* value,size_t size){
 unsigned char hash[32];unsigned int len=0;
 if(!HMAC(EVP_sha256(),digestKey_->data(),32,reinterpret_cast<const unsigned char*>(value),size,hash,&len) || len!=32)throw std::runtime_error("digest_failed");
 std::string result(reinterpret_cast<const char*>(hash),32);wipe(hash,sizeof(hash));return result;
}
void AuthStore::retireLocked(){
 if(!current_)return;
 {std::lock_guard<std::mutex> lock(current_->mutex);
  if(current_->packet && current_->supplied && retired_.size()<32)retired_.push_back(fingerprint(current_->packet->data()+current_->offsets[0],current_->lengths[0]));}
 current_->revoke();current_.reset();
}
Json AuthStore::begin(uid_t uid,const std::string& mode,unsigned ttl){
 std::lock_guard<std::mutex> lock(mutex_);
 if(!principal(bindings_,uid))return err("unauthorized_operator");
 if(current_ && !current_->valid())retireLocked();
 if(current_)return err("session_busy");
 if(retired_.size()>=32)return err("session_limit");
 if(ttl<10 || ttl>300 || (mode!="fixture" && mode!="live_read_only"))return err("invalid_session");
 if(mode=="live_read_only" && !bindings_.readOnly)return err("live_platform_unverified");
 current_=std::make_shared<Lease>();current_->id=randomId();current_->mode=mode;current_->until=Mono::now()+std::chrono::seconds(ttl);
 return {{"status","supply_required"},{"session_id",current_->id},{"ttl_seconds",ttl},{"mode",mode}};
}
Json AuthStore::supply(uid_t uid,const std::string& id,std::unique_ptr<SecretPages> page,size_t n){
 std::lock_guard<std::mutex> lock(mutex_);
 if(!principal(bindings_,uid))return err("unauthorized_operator");
 if(!current_ || current_->id!=id || !current_->valid())return err("session_expired_or_changed");
 std::lock_guard<std::mutex> leaseLock(current_->mutex);
 if(current_->supplied)return err("duplicate_supply");
 // SUP1 + session ID (32 ASCII hex) + 3 big-endian lengths + opaque header values.
 if(n<48 || n>8192 || memcmp(page->data(),"SUP1",4) || memcmp(page->data()+4,id.data(),32))return err("invalid_supply_packet");
 size_t offset=48;
 for(size_t i=0;i<3;i++){
  uint32_t raw;memcpy(&raw,page->data()+36+i*4,4);const size_t length=ntohl(raw);
  const size_t max=i==0?4096:(i==1?128:1024);
  if(!length || length>max || length>n-offset || !header(page->data()+offset,length))return err("invalid_supply_packet");
  current_->offsets[i]=offset;current_->lengths[i]=length;offset+=length;
 }
 if(offset!=n || current_->lengths[0]<8)return err("invalid_supply_packet");
 const auto digest=fingerprint(page->data()+48,current_->lengths[0]);
 for(const auto& old:retired_)if(CRYPTO_memcmp(old.data(),digest.data(),32)==0)return err("credential_retired_in_this_process");
 current_->packet=std::move(page);current_->supplied=true;
 return {{"status","credential_received"},{"session_id",id},{"mode",current_->mode},{"manual_armed",false}};
}
Json AuthStore::arm(uid_t uid,const std::string& id,bool approved){
 std::lock_guard<std::mutex> lock(mutex_);
 if(!principal(bindings_,uid) || !current_ || current_->id!=id || !current_->valid() || !current_->supplied)return err("session_unavailable");
 if(!approved || !bindings_.manual || current_->mode!="live_read_only")return err("manual_not_authorized");
 std::lock_guard<std::mutex> leaseLock(current_->mutex);current_->mode="live_manual";
 return {{"status","manual_session_armed"},{"session_id",id},{"mode","live_manual"}};
}
void AuthStore::revoke(){std::lock_guard<std::mutex> lock(mutex_);retireLocked();}
void AuthStore::tick(){std::lock_guard<std::mutex> lock(mutex_);if(current_ && !current_->valid())retireLocked();}
Json AuthStore::status(){
 std::lock_guard<std::mutex> lock(mutex_);
 if(!bindings_.verified)return {{"status","platform_binding_unverified"},{"post_enabled",false}};
 if(!current_ || !current_->valid())return {{"status","credential_unavailable"},{"post_enabled",false}};
 std::lock_guard<std::mutex> leaseLock(current_->mutex);
 return {{"status",current_->supplied?"credential_active":"awaiting_credential"},{"session_id",current_->id},{"mode",current_->mode},
 {"expires_in_seconds",std::max<int64_t>(0,std::chrono::duration_cast<std::chrono::seconds>(current_->until-Mono::now()).count())},
 {"post_enabled",current_->supplied && current_->mode=="live_manual"}};
}
std::shared_ptr<Lease> AuthStore::lease(const std::string& id,const std::string& mode,uid_t uid){
 std::lock_guard<std::mutex> lock(mutex_);
 if(!bindings_.verified || uid==0 || uid!=bindings_.ui || uid==bindings_.operatorUid || !current_ || current_->id!=id || !current_->valid())return {};
 std::lock_guard<std::mutex> leaseLock(current_->mutex);
 if(!current_->supplied || current_->mode!=mode || mode=="fixture")return {};
 return current_;
}
std::string credentialEndpoint(uid_t uid,pid_t pid){return "deskmate.thinqpoc1.credential."+std::to_string(uid)+"."+std::to_string(pid);}
int connectVerified(const std::string& name,uid_t expectedUid,pid_t expectedPid){
 if(expectedUid==0 || expectedPid<2 || getuid()!=geteuid() || getuid()==0)return -1;
 const int fd=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0);if(fd<0)return -1;
 socklen_t n;const auto a=address(name,n);ucred c{};
 if(connect(fd,reinterpret_cast<const sockaddr*>(&a),n) || !peer(fd,c) || c.uid!=expectedUid || c.pid!=expectedPid){close(fd);return -1;}
 return fd;
}
bool sendPacket(int fd,const void* data,size_t n){return n<=8192 && send(fd,data,n,MSG_NOSIGNAL)==static_cast<ssize_t>(n);}
bool receivePacket(int fd,SecretPages& page,size_t& n,int timeout){
 pollfd p{fd,POLLIN,0};if(poll(&p,1,timeout)<=0)return false;
 const auto size=recv(fd,nullptr,0,MSG_PEEK|MSG_TRUNC);
 if(size<=0 || static_cast<size_t>(size)>page.size() || size>8192)return false;
 const auto r=recv(fd,page.data(),static_cast<size_t>(size),0);if(r!=size)return false;n=static_cast<size_t>(r);return true;
}
CredentialServer::CredentialServer(AuthStore& store,std::string name):store_(store),endpoint_(std::move(name)){}
CredentialServer::~CredentialServer(){stop();}
bool CredentialServer::start(){
#ifndef POC_HOST_TEST
 if(store_.bindings().operatorUid==store_.bindings().service)return false;
#endif
 if(!store_.bindings().verified || getuid()==0 || getuid()!=store_.bindings().service || store_.bindings().ui==getuid() || store_.bindings().operatorUid==store_.bindings().ui)return false;
 listener_=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC|SOCK_NONBLOCK,0);if(listener_<0)return false;
 socklen_t n;const auto a=address(endpoint_,n);
 if(bind(listener_,reinterpret_cast<const sockaddr*>(&a),n) || listen(listener_,4)){close(listener_);listener_=-1;return false;}
 stop_=false;thread_=std::thread([this](){loop();});return true;
}
void CredentialServer::stop(){stop_=true;if(thread_.joinable())thread_.join();if(listener_>=0){close(listener_);listener_=-1;}store_.revoke();}
void CredentialServer::loop(){
 int active=-1;uid_t uid=0;std::string sid;auto handshake=Mono::now();
 auto disconnect=[&](){if(active>=0)close(active);active=-1;sid.clear();store_.revoke();};
 while(!stop_){
  store_.tick();pollfd fds[2]{{listener_,POLLIN,0},{active,POLLIN,0}};
  const int ready=poll(fds,2,50);if(ready<0){if(errno==EINTR)continue;break;}
  if(fds[0].revents&POLLIN){
   const int fd=accept4(listener_,nullptr,nullptr,SOCK_CLOEXEC);ucred c{};
   if(fd>=0){
    if(!peer(fd,c) || !principal(store_.bindings(),c.uid)){const auto r=err("unauthorized_operator").dump();sendPacket(fd,r.data(),r.size());close(fd);}
    else if(active>=0){const auto r=err("session_busy").dump();sendPacket(fd,r.data(),r.size());close(fd);}
    else {active=fd;uid=c.uid;handshake=Mono::now();}
   }
  }
  if(active<0)continue;
  if((sid.empty() && Mono::now()-handshake>std::chrono::seconds(3)) || (!sid.empty() && store_.status().value("status","")=="credential_unavailable")){disconnect();continue;}
  if(fds[1].revents&(POLLIN|POLLHUP|POLLERR)){
   try{
    auto packet=std::make_unique<SecretPages>(8192);size_t n=0;
    if(!receivePacket(active,*packet,n,100)){disconnect();continue;}
    Json result;
    if(n>=4 && !memcmp(packet->data(),"SUP1",4))result=store_.supply(uid,sid,std::move(packet),n);
    else {
     // Metadata has a bounded, strict vocabulary. Never reflect input or exceptions.
     const auto request=Json::parse(packet->data(),packet->data()+n,nullptr,false);
     if(!request.is_object() || request.dump().size()>1024){result=err("invalid_metadata");}
     else if(request.value("op","")=="begin" && sid.empty()){
      result=store_.begin(uid,request.value("mode",std::string()),request.value("ttl",0u));if(result.contains("session_id"))sid=result["session_id"].get<std::string>();
     }else if(request.value("op","")=="arm_manual" && request.value("session_id","")==sid)result=store_.arm(uid,sid,request.value("approved",false));
     else if(request.value("op","")=="revoke" && request.value("session_id","")==sid){store_.revoke();result={{"status","revoked"}};}
     else result=err("invalid_metadata");
    }
    const auto out=result.dump();if(!sendPacket(active,out.data(),out.size()))disconnect();
    if(result.value("status","")=="revoked")disconnect();
   }catch(...){disconnect();}
  }
 }
 disconnect();
}
