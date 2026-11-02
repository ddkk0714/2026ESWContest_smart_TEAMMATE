#include "https.h"
#include <cassert>
#include <iostream>
int main() {
 int calls=0;
 auto fake=[&](const HttpsRequest&,const Credentials&){++calls;return Json{{"response",Json::object()}};};
 GuardedHttps blocked(Credentials{},fake);
 assert(blocked.request({"GET","/devices",nullptr}).contains("error"));assert(calls==0);
 Credentials manual{"fixture-token","fixture-client","fixture-application",true};manual.mode="live_manual";
 GuardedHttps api(std::move(manual),fake);
 assert(!api.request({"GET","/devices",nullptr}).contains("error"));
 assert(!api.request({"GET","/devices/fixture/profile",nullptr}).contains("error"));
 for(const auto& path:{"/devices/other/state","https://invalid.example/devices","/devices/fixture/state?x=1","/devices/fixture/../control"})
   assert(api.request({"GET",path,nullptr}).contains("error"));
 assert(api.request({"DELETE","/devices/fixture/state",nullptr}).contains("error"));
 Json low={{"airFlow",{{"windStrength","LOW"}}}};
 assert(api.request({"POST","/devices/fixture/control",low}).contains("error"));
 api.approve("LOW");assert(api.request({"POST","/devices/fixture/control",{{"airFlow",{{"windStrengthDetail","LOW"}}}}}).contains("error"));
 assert(api.request({"POST","/devices/fixture/control",low}).contains("error"));
 api.approve("LOW");assert(!api.request({"POST","/devices/fixture/control",low}).contains("error"));
 assert(api.request({"POST","/devices/fixture/control",low}).contains("error"));
 assert(calls==3);std::cout<<"transport guards: passed; real network calls: 0\n";
}
