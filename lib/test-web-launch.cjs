// Shell-free test-only entrypoint. Keep the database guard fail-closed.
const {main}=require("./test-web-guard.cjs");
main().then(()=>{
  require("/app/server.js");
}).catch(()=>{
  console.error("Test Web guard or startup rejected configuration");
  process.exitCode=1;
});
