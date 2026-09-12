import http from "k6/http";
import { sleep } from "k6";

const baseURL = __ENV.BASE_URL || "https://eps.localtest.me:8443";
export const options = {
  insecureSkipTLSVerify: __ENV.ALLOW_SELF_SIGNED === "true",
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
  http.get(`${baseURL}/`);
  sleep(1);
}
