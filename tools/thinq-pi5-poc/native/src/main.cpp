#include "https.h"
#include "auth.h"
#include "bindings.h"
#include <pwd.h>
#include "embedded.h"
#ifndef POC_HOST_TEST
#include <sdbus-c++/sdbus-c++.h>
#endif
#include <sys/prctl.h>
#include <sys/wait.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <csignal>
#include <chrono>
#include <iostream>
#include <thread>
#include <set>
int runCredentialHelper(int,char**);
using Clock=std::chrono::steady_clock;
namespace {
volatile sig_atomic_t stopped=0;
void stop(int) { stopped=1; }
constexpr const char* id="com.deskmate.thinqpoc1";
Json failure(const char* reason) {return {{"status","blocked"},{"error",reason},{"post_enabled",false},{"automatic_control",false}};}
void reap(pid_t child) {
 kill(-child,SIGTERM);
 int status=0;
 for(int n=0;n<10;n++) {if(waitpid(child,&status,WNOHANG)==child)return;std::this_thread::sleep_for(std::chrono::milliseconds(20));}
 kill(-child,SIGKILL);while(waitpid(child,&status,0)<0 && errno==EINTR){}
}
bool sendLine(int fd,const std::string& line,Clock::time_point deadline) {
 size_t n=0;
 while(n<line.size() && !stopped && Clock::now()<deadline) {
  pollfd p{fd,POLLOUT,0};if(poll(&p,1,100)<=0)continue;
  const auto wrote=write(fd,line.data()+n,line.size()-n);
  if(wrote>0)n+=static_cast<size_t>(wrote);else if(errno!=EINTR && errno!=EAGAIN)return false;
 }
 return n==line.size();
}
RoleBindings loadBindings(){
 RoleBindings b;
 if(!kBindingsConfigured)return b;
 auto uid=[](const char* name)->uid_t {const auto* p=getpwnam(name);return p?p->pw_uid:static_cast<uid_t>(-1);};
 b.service=uid(kServiceUser);b.operatorUid=uid(kOperatorUser);b.ui=uid(kUiUser);
 const auto missing=static_cast<uid_t>(-1);
 b.verified=b.service!=0 && b.operatorUid!=0 && b.ui!=0 && b.service!=missing && b.operatorUid!=missing && b.ui!=missing &&
   getuid()==geteuid() && getuid()==b.service && b.service!=b.operatorUid && b.service!=b.ui && b.operatorUid!=b.ui;
 b.readOnly=b.verified && kReadOnlyVerified;b.manual=b.readOnly && kManualVerified;return b;
}
Json worker(const Json& request,Json& context,const char* python,bool host,AuthStore* auth=nullptr,uid_t caller=static_cast<uid_t>(-1)) {
 if(!request.is_object()) return failure("invalid_request");
 const auto backend=request.value("backend",Json("fixture"));
 const bool live=backend=="live_read_only" || backend=="live_manual";
 if(backend=="live")return failure("live_credentials_and_policy_unverified"); // old ambiguous mode never enabled
 if(backend!="fixture" && !live)return failure("invalid_backend");
 std::shared_ptr<Lease> lease;
 if(live){
  if(!auth || !auth->bindings().verified)return failure("live_credentials_and_policy_unverified");
  lease=auth->lease(request.value("auth_session_id",std::string()),backend.get<std::string>(),caller);
  if(!lease)return failure("credential_session_or_caller_denied");
  if(backend=="live_read_only" && request.value("op",Json())!="read")return failure("read_only_operation_blocked");
  // New credential/mode authorization discards old nonces, retaining only same-session target pin.
  if(context.value("auth_session_id",std::string())!=lease->id || context.value("backend",Json())!=backend){
   Json target=context.value("private_target",Json());const bool same=context.value("auth_session_id",std::string())==lease->id;
   context=Json::object();if(same && target.is_string())context["private_target"]=target;
  }
  context["auth_session_id"]=lease->id;context["backend"]=backend;context["enabled"]=backend=="live_manual";
 }else if(context.contains("auth_session_id"))context=Json::object();
 if(request.dump().size()>4096 || context.dump().size()>16384)return failure("request_too_large");
 const std::set<std::string> allowed{"op","backend","enabled","value","nonce","approved","physical_online_confirmed","no_external_change","auth_session_id"};
 for(auto it=request.begin();it!=request.end();++it)if(!allowed.count(it.key()))return failure("unexpected_request_fields");
 if(request.contains("value") && request["value"]!="LOW" && request["value"]!="AUTO")return failure("invalid_control_value");
 auto childContext=context;
 if(request.value("op",Json())=="execute") { context.erase("challenge");context["uncertain"]=true; }
 int input[2],output[2];if(pipe2(input,O_CLOEXEC)!=0)return failure("pipe_failed");
 if(pipe2(output,O_CLOEXEC)!=0){close(input[0]);close(input[1]);return failure("pipe_failed");}
 const auto parent=getpid();const auto pid=fork();
 if(pid==0) {
  setpgid(0,0);prctl(PR_SET_PDEATHSIG,SIGTERM);if(getppid()!=parent)_exit(127);
  dup2(input[0],STDIN_FILENO);dup2(output[1],STDOUT_FILENO);
  const int nullfd=open("/dev/null",O_WRONLY);dup2(nullfd,STDERR_FILENO);close(nullfd);
  close(input[0]);close(input[1]);close(output[0]);close(output[1]);
  char* const args[]={const_cast<char*>(python),const_cast<char*>("-s"),const_cast<char*>("-B"),const_cast<char*>("-u"),const_cast<char*>("-c"),const_cast<char*>(kWorker),nullptr};
  char* const env[]={const_cast<char*>("PATH=/usr/bin:/bin"),const_cast<char*>("LANG=C"),const_cast<char*>("PYTHONNOUSERSITE=1"),
   const_cast<char*>("PYTHONDONTWRITEBYTECODE=1"),const_cast<char*>("PYTHONHOME=/restricted/python3/usr"),
   const_cast<char*>("LD_LIBRARY_PATH=/restricted/python3/usr/lib"),
   const_cast<char*>("PYTHONPATH=/usr/lib/python3.12/site-packages:/usr/lib/python3.12:/usr/lib/python3.12/lib-dynload"),nullptr};
  char* const hostEnv[]={const_cast<char*>("PATH=/usr/bin:/bin"),const_cast<char*>("PYTHONDONTWRITEBYTECODE=1"),nullptr};
  execve(python,args,host?hostEnv:env);_exit(126);
 }
 close(input[0]);close(output[1]);
 if(pid<0){close(input[1]);close(output[0]);return failure("fork_failed");}
 setpgid(pid,pid);fcntl(input[1],F_SETFL,O_NONBLOCK);fcntl(output[0],F_SETFL,O_NONBLOCK);
 const auto deadline=Clock::now()+std::chrono::seconds(25);
 auto cleanup=[&](){close(input[1]);close(output[0]);reap(pid);};
 if(!sendLine(input[1],Json{{"request",request},{"context",childContext}}.dump()+"\n",deadline)) {cleanup();return failure("worker_input_failed");}
 std::string buffer;Json result=failure("worker_timeout_or_runtime_unavailable");
 // No plaintext credential copies exist before fork. Mapped token pages are DONTFORK.
 Credentials credentials;
 if(live){
  credentials=lease->credentials();
  credentials.authorized=[lease](){return lease->valid() && !stopped;};
 }
 GuardedHttps https(std::move(credentials));
 bool postAttempted=false;
 int httpCalls=0;
 while(!stopped && Clock::now()<deadline && (!live || lease->valid())) {
  pollfd p{output[0],POLLIN,0};if(poll(&p,1,100)<=0)continue;
  char chunk[4096];const auto n=read(output[0],chunk,sizeof(chunk));if(n<=0)break;
  buffer.append(chunk,static_cast<size_t>(n));if(buffer.size()>1024*1024){result=failure("output_limit");break;}
  size_t end;
  while((end=buffer.find('\n'))!=std::string::npos) {
   auto frame=Json::parse(buffer.substr(0,end),nullptr,false);buffer.erase(0,end+1);
   if(!frame.is_object()){result=failure("worker_protocol_error");goto done;}
   if(frame.value("kind","")=="reply") {
    if(!frame.contains("public") || !frame["public"].is_object() || !frame.contains("context") || !frame["context"].is_object())goto done;
    if(frame["context"].contains("enabled") && frame["context"].contains("attempted")) context=frame["context"];
    result=frame["public"];goto done;
   }
   if(frame.value("kind","")=="http") {
    // Fixture mode MUST never issue HTTP, even if a worker regresses.
    if(!live || ++httpCalls>12) {result=failure("fixture_network_blocked");goto done;}
    if(!frame.contains("method") || !frame["method"].is_string() || !frame.contains("path") || !frame["path"].is_string())goto done;
    const auto method=frame["method"].get<std::string>();
    const auto payload=frame.value("payload",Json());
    if(method=="POST") {
     const auto challenge=childContext.value("challenge",Json::object());
     if(postAttempted || request.value("op",Json())!="execute" || request.value("approved",Json())!=true ||
        request.value("physical_online_confirmed",Json())!=true || request.value("no_external_change",Json())!=true ||
        !challenge.is_object() || request.value("nonce",Json())!=challenge.value("nonce",Json())) {
      result=failure("post_not_approved");goto done;
     }
     postAttempted=true; // native boundary also refuses a second attempt
     const auto value=challenge.value("value",Json());
     if(!value.is_string())goto done;
     https.approve(value.get<std::string>());
    }
    const auto response=https.request({method,frame["path"].get<std::string>(),payload});
    if(response.value("http_status",Json())==401 || response.value("http_status",Json())==403){
     auth->revoke();context=Json::object();result={{"status","api_error"},{"http_status",response["http_status"]},{"post_enabled",false},{"automatic_resend",false}};goto done;
    }
    if(!sendLine(input[1],response.dump()+"\n",deadline)){result=failure("worker_input_failed");goto done;}
   }
  }
 }
 done:
 if(live && !lease->valid()){
  context=Json::object();
  if(!result.contains("http_status"))result=failure("authorization_expired_or_revoked");
  result["outcome_unknown"]=postAttempted;result["automatic_resend"]=false;
 }
 cleanup();return result;
}
}
int main(int argc,char** argv) {
 if(argc>1 && std::string(argv[1])=="--credential-stdin")return runCredentialHelper(argc-1,argv+1);
 std::signal(SIGINT,stop);std::signal(SIGTERM,stop);std::signal(SIGPIPE,SIG_IGN);
 Json context=Json::object();
 try {
 RoleBindings bindings=loadBindings();if(!hardenProcess())bindings.verified=false;
 AuthStore auth(bindings);
#ifdef POC_HOST_TEST
  if(argc==3 && std::string(argv[1])=="--stdio-host-fixture") {
   std::string line;while(!stopped && std::getline(std::cin,line)) {
    auto req=Json::parse(line,nullptr,false);std::cout<<worker(req,context,argv[2],true).dump()<<std::endl;
   }
   return 0;
  }
#else
  if(argc!=1)return 64;
#endif
 #ifndef POC_HOST_TEST
  auto bus=sdbus::createSystemBusConnection(sdbus::ServiceName{id});
  CredentialServer credentials(auth,credentialEndpoint(getuid(),getpid()));
  auto object=sdbus::createObject(*bus,sdbus::ObjectPath{"/com/deskmate/thinqpoc1"});
  object->addVTable(sdbus::registerMethod("Exchange").implementedAs([&](const std::string& input){
   try{
    const auto caller=object->getCurrentlyProcessedMessage().getCredsEuid();
    if(!bindings.verified || caller==0 || caller!=bindings.ui)return failure("caller_policy_unverified_or_denied").dump();
    return worker(Json::parse(input),context,"/restricted/python3/usr/bin/python3",false,&auth,caller).dump();
   }
   catch(...){return failure("invalid_request").dump();}
  }),sdbus::registerMethod("AuthStatus").implementedAs([&](){
   try{
    const auto caller=object->getCurrentlyProcessedMessage().getCredsEuid();
    if(bindings.verified && caller!=0 && (caller==bindings.ui || caller==bindings.operatorUid))return auth.status().dump();
    return failure("caller_policy_unverified_or_denied").dump();
   }catch(...){return failure("caller_identity_unavailable").dump();}
  })).forInterface(sdbus::InterfaceName{id});
  if(bindings.verified && !credentials.start())return 1;
  bus->enterEventLoopAsync();const auto deadline=Clock::now()+std::chrono::minutes(10);
  while(!stopped && Clock::now()<deadline){auth.tick();std::this_thread::sleep_for(std::chrono::milliseconds(100));}
  credentials.stop();
  bus->leaveEventLoop();return 0;
#else
  return 64;
#endif
 } catch(...) {std::cerr<<"thinqpoc_service_setup_failed"<<std::endl;return 1;}
}
