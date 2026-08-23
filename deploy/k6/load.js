// Load test against the dashboard route, through the ingress like a user.
// Run with: k6 run deploy/k6/load.js
import http from "k6/http";
import { sleep } from "k6";

export const options = {
  // The certificate is self-signed by design.
  insecureSkipTLSVerify: true,
  stages: [
    { duration: "1m", target: 10 },
    { duration: "2m", target: 50 },
    { duration: "2m", target: 200 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<1000"],
  },
};

export default function () {
  http.get("https://eps.localtest.me/");
  sleep(1);
}
