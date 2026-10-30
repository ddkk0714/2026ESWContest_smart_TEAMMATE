#pragma once
#include "https.h"
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <thread>
#include <sys/types.h>
#include <vector>

using Mono = std::chrono::steady_clock;
void wipe(void*,size_t) noexcept;
bool hardenProcess() noexcept;
class SecretPages {
 char* data_=nullptr; size_t size_=0;
public:
 explicit SecretPages(size_t);
 ~SecretPages();
 SecretPages(const SecretPages&)=delete;
 SecretPages& operator=(const SecretPages&)=delete;
 char* data() noexcept {return data_;}
 const char* data() const noexcept {return data_;}
 size_t size() const noexcept {return size_;}
 void clear() noexcept;
};
struct RoleBindings {
 bool verified=false, readOnly=false, manual=false;
 uid_t service=static_cast<uid_t>(-1), operatorUid=static_cast<uid_t>(-1), ui=static_cast<uid_t>(-1);
};
struct Lease {
 mutable std::mutex mutex;
 std::string id,mode;
 Mono::time_point until;
 std::atomic<bool> cancelled{false};
 std::unique_ptr<SecretPages> packet;
 size_t lengths[3]{}, offsets[3]{};
 bool supplied=false;
 bool valid() const noexcept;
 Credentials credentials(); // Called ONLY after worker fork; child never receives credentials.
 void revoke() noexcept;
};
class AuthStore {
 RoleBindings bindings_;
 std::mutex mutex_;
 std::shared_ptr<Lease> current_;
 std::unique_ptr<SecretPages> digestKey_;
 std::vector<std::string> retired_;
 void retireLocked();
 std::string fingerprint(const char*,size_t);
public:
 explicit AuthStore(RoleBindings);
 ~AuthStore();
 Json begin(uid_t,const std::string&,unsigned);
 Json supply(uid_t,const std::string&,std::unique_ptr<SecretPages>,size_t);
 Json arm(uid_t,const std::string&,bool);
 void revoke();
 void tick();
 Json status();
 std::shared_ptr<Lease> lease(const std::string&,const std::string&,uid_t);
 const RoleBindings& bindings() const {return bindings_;}
};
std::string credentialEndpoint(uid_t,pid_t);
int connectVerified(const std::string&,uid_t,pid_t);
bool sendPacket(int,const void*,size_t);
bool receivePacket(int,SecretPages&,size_t&,int);
class CredentialServer {
 AuthStore& store_;
 std::string endpoint_;
 int listener_=-1;
 std::atomic<bool> stop_{false};
 std::thread thread_;
 void loop();
public:
 CredentialServer(AuthStore&,std::string);
 ~CredentialServer();
 bool start();
 void stop();
};
