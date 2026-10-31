// Host-only harness. NOT linked into any IPK. Injected UI identity is a test double.
#include "auth.h"
#include <sys/wait.h>
#include <sys/mman.h>
#include <unistd.h>
#include <iostream>
#include <thread>
#include <atomic>
int main(int argc,char** argv){
 if(!hardenProcess() || getuid()==0)return 2;
 try{
  RoleBindings b;b.verified=true;b.service=getuid();b.operatorUid=argc>1?static_cast<uid_t>(std::stoul(argv[1])):getuid();b.ui=getuid()+1;b.readOnly=true;b.manual=true;
  AuthStore auth(b);CredentialServer receiver(auth,credentialEndpoint(getuid(),getpid()));if(!receiver.start())return 2;
  std::cout<<Json{{"pid",getpid()},{"uid",getuid()},{"endpoint",credentialEndpoint(getuid(),getpid())}}.dump()<<std::endl;
  int calls=0,posts=0;std::string line;
  while(std::getline(std::cin,line)){
   auto r=Json::parse(line);const auto op=r.value("op","");
   Json out;
   if(op=="status")out=auth.status();
   else if(op=="revoke"){auth.revoke();out={{"status","revoked"}};}
   else{
    auto lease=auth.lease(r.value("session_id",std::string()),r.value("mode",std::string()),static_cast<uid_t>(r.value("caller_uid",static_cast<unsigned>(b.ui))));
    if(!lease)out={{"error","authorization_denied"}};
    else if(op=="dontfork"){
     const auto pid=fork();if(pid==0){unsigned char vec=0;const auto ok=mincore(lease->packet->data(),4096,&vec)<0 && errno==ENOMEM;_exit(ok?0:1);}int status;waitpid(pid,&status,0);out={{"mapped_secret_not_inherited",WIFEXITED(status)&&WEXITSTATUS(status)==0}};
    }else{
     auto c=lease->credentials();c.authorized=[lease](){return lease->valid();};
     GuardedHttps https(std::move(c),[&](const HttpsRequest& req,const Credentials& creds){
      ++calls;if(req.method=="POST")++posts;
      if(op=="slow")for(int n=0;n<200;n++){if(!creds.valid())return Json{{"error","authorization_expired_or_revoked"}};std::this_thread::sleep_for(std::chrono::milliseconds(10));}
      return Json{{"response",Json::object()}};
     });
     (void)https.request({"GET","/devices/fixture/profile",nullptr});
     if(op=="post")https.approve("LOW");
     out=https.request({(op=="post" || op=="post_unapproved")?"POST":"GET",(op=="post" || op=="post_unapproved")?"/devices/fixture/control":"/devices/fixture/state",(op=="post" || op=="post_unapproved")?Json{{"airFlow",{{"windStrength","LOW"}}}}:Json()});
     out["calls"]=calls;out["post_calls"]=posts;
    }
   }
   std::cout<<out.dump()<<std::endl;
  }
  receiver.stop();return 0;
 }catch(...){std::cerr<<"auth_harness_failed"<<std::endl;return 2;}
}
