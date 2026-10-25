#pragma once
#include <nlohmann/json.hpp>
#include <functional>
#include <string>
using Json = nlohmann::json;
struct Credentials { std::string pat, clientId, apiKey; bool verifiedProvider = false; };
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
