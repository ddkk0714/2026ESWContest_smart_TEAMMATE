#include "https.h"
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
struct RuntimeAccess {
 Credentials credentials;
 bool callerPolicyVerified=false;
};
// Deliberate integration boundary: no credential source or policy attestation is invented.
// A separately approved board-specific provider must replace this empty result.
RuntimeAccess verifiedRuntimeAccess() { return {}; }
Json worker(const Json& request,Json& context,const char* python,bool host) {
 const auto access=verifiedRuntimeAccess();
 if(!request.is_object()) return failure("invalid_request");
 const auto backend=request.value("backend",Json("fixture"));
 const bool live=backend=="live";
 if(backend!="fixture" && !live) return failure("invalid_backend");
 if(live && (!access.credentials.verifiedProvider || !access.callerPolicyVerified))
  return failure("live_credentials_and_policy_unverified");
 if(request.dump().size()>4096 || context.dump().size()>16384)return failure("request_too_large");
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
 GuardedHttps https(access.credentials);
 bool postAttempted=false;
 int httpCalls=0;
 while(!stopped && Clock::now()<deadline) {
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
    if(!sendLine(input[1],response.dump()+"\n",deadline)){result=failure("worker_input_failed");goto done;}
   }
  }
 }
 done: cleanup();return result;
}
}
int main(int argc,char** argv) {
 std::signal(SIGINT,stop);std::signal(SIGTERM,stop);std::signal(SIGPIPE,SIG_IGN);
 Json context=Json::object();
 try {
#ifdef POC_HOST_TEST
  if(argc==3 && std::string(argv[1])=="--stdio-host-fixture") {
   std::string line;while(!stopped && std::getline(std::cin,line)) {
    auto req=Json::parse(line,nullptr,false);std::cout<<worker(req,context,argv[2],true).dump()<<std::endl;
   }
   return 0;
  }
#else
  (void)argc;(void)argv;
#endif
 #ifndef POC_HOST_TEST
  auto bus=sdbus::createSystemBusConnection(sdbus::ServiceName{id});
  auto object=sdbus::createObject(*bus,sdbus::ObjectPath{"/com/deskmate/thinqpoc1"});
  object->addVTable(sdbus::registerMethod("Exchange").implementedAs([&](const std::string& input){
   try{return worker(Json::parse(input),context,"/restricted/python3/usr/bin/python3",false).dump();}
   catch(...){return failure("invalid_request").dump();}
  })).forInterface(sdbus::InterfaceName{id});
  bus->enterEventLoopAsync();const auto deadline=Clock::now()+std::chrono::minutes(10);
  while(!stopped && Clock::now()<deadline)std::this_thread::sleep_for(std::chrono::milliseconds(100));
  bus->leaveEventLoop();return 0;
#else
  return 64;
#endif
 } catch(...) {std::cerr<<"thinqpoc_service_setup_failed"<<std::endl;return 1;}
}
