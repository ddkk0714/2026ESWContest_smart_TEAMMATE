// SSH remote command only. No token argv, environment lookup, filesystem or D-Bus token RPC.
#include "auth.h"
#include <pwd.h>
#include <unistd.h>
#include <poll.h>
#include <arpa/inet.h>
#include <csignal>
#include <cstring>
#include <iostream>
#include <regex>
namespace {
volatile sig_atomic_t stopped=0;
void stop(int){stopped=1;}
bool readExact(int fd,char* data,size_t size){
 size_t used=0;const auto until=Mono::now()+std::chrono::seconds(30);
 while(used<size && !stopped && Mono::now()<until){pollfd p{fd,POLLIN,0};if(poll(&p,1,100)<=0)continue;const auto n=read(fd,data+used,size-used);if(n<=0)return false;used+=static_cast<size_t>(n);}
 return used==size;
}
Json response(int fd,SecretPages& page){size_t n=0;if(!receivePacket(fd,page,n,5000))return {{"status","blocked"},{"error","receiver_unavailable"}};auto j=Json::parse(page.data(),page.data()+n,nullptr,false);page.clear();if(!j.is_object())return {{"status","blocked"},{"error","bad_protocol"}};return j;}
// Never echo arbitrary JSON or error text received from a peer, even our service.
Json publicReply(const Json& value){
 const auto status=value.value("status",std::string());
 if(status!="supply_required" && status!="credential_received" && status!="manual_session_armed")return {{"status","blocked"}};
 const auto sid=value.value("session_id",std::string());const auto mode=value.value("mode",std::string());
 if(!std::regex_match(sid,std::regex("^[a-f0-9]{32}$")) || (mode!="fixture" && mode!="live_read_only" && mode!="live_manual"))return {{"status","blocked"}};
 return {{"status",status},{"session_id",sid},{"mode",mode}};
}
bool sendJson(int fd,const Json& j){const auto s=j.dump();return sendPacket(fd,s.data(),s.size());}
}
int runCredentialHelper(int argc,char** argv){
 std::signal(SIGPIPE,SIG_IGN);std::signal(SIGTERM,stop);std::signal(SIGINT,stop);
 try{
  if(!hardenProcess() || getuid()==0 || getuid()!=geteuid())throw std::runtime_error("unsafe_identity");
  std::string endpoint,mode;uid_t uid;pid_t pid;unsigned ttl;
#ifdef POC_HOST_TEST
  if(argc==7 && std::string(argv[1])=="--host-fixture"){
   endpoint=argv[2];uid=static_cast<uid_t>(std::stoul(argv[3]));pid=static_cast<pid_t>(std::stol(argv[4]));mode=argv[5];ttl=static_cast<unsigned>(std::stoul(argv[6]));
  }else
#endif
  {
   if(argc!=5 || !std::regex_match(argv[1],std::regex("^u0_a[0-9]+$")))throw std::runtime_error("invalid_arguments");
   const auto* account=getpwnam(argv[1]);if(!account || account->pw_uid==0)throw std::runtime_error("unknown_service_account");
   uid=account->pw_uid;pid=static_cast<pid_t>(std::stol(argv[2]));mode=argv[3];ttl=static_cast<unsigned>(std::stoul(argv[4]));endpoint=credentialEndpoint(uid,pid);
  }
  if(ttl<10 || ttl>300 || (mode!="fixture" && mode!="live_read_only"))throw std::runtime_error("invalid_mode");
  SecretPages packet(8192); // protection must succeed BEFORE READY or reading any token
  const int fd=connectVerified(endpoint,uid,pid);if(fd<0)throw std::runtime_error("server_identity_or_permission_failed");
  struct Close{int fd;~Close(){close(fd);}} closer{fd};
  sendJson(fd,{{"op","begin"},{"mode",mode},{"ttl",ttl}});auto reply=response(fd,packet);
  if(reply.value("status","")!="supply_required"){std::cout<<publicReply(reply).dump()<<std::endl;return 2;}
  const auto sid=reply.value("session_id",std::string());if(!std::regex_match(sid,std::regex("^[a-f0-9]{32}$")))throw std::runtime_error("bad_session");
  std::cout<<publicReply(reply).dump()<<std::endl; // only authenticated non-secret metadata
  uint32_t raw;
  if(!readExact(STDIN_FILENO,reinterpret_cast<char*>(&raw),4))throw std::runtime_error("supply_cancelled");
  const auto n=ntohl(raw);if(n<48 || n>8192 || !readExact(STDIN_FILENO,packet.data(),n))throw std::runtime_error("invalid_supply");
  if(!sendPacket(fd,packet.data(),n)){throw std::runtime_error("supply_failed");}
  packet.clear();reply=response(fd,packet);
  std::cout<<publicReply(reply).dump()<<std::endl;if(reply.value("status","")!="credential_received")return 2;
  // Keep SSH stdin open for the experiment. EOF, signal, expiry or revoke drops the lease.
  while(!stopped){
   pollfd fds[2]{{STDIN_FILENO,POLLIN,0},{fd,POLLIN,0}};if(poll(fds,2,100)<=0)continue;
   if(fds[1].revents){std::cout<<"{\"status\":\"credential_channel_closed\"}"<<std::endl;break;}
   if(fds[0].revents&(POLLIN|POLLHUP|POLLERR)){
    char command=0;if(read(STDIN_FILENO,&command,1)!=1)break;
    if(command=='M'){
     sendJson(fd,{{"op","arm_manual"},{"session_id",sid},{"approved",true}});reply=response(fd,packet);std::cout<<publicReply(reply).dump()<<std::endl;
    }else if(command=='R')break;
   }
  }
  sendJson(fd,{{"op","revoke"},{"session_id",sid}});return 0;
 }catch(...){std::cerr<<"credential_delivery_blocked"<<std::endl;return 2;}
}
