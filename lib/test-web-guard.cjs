// Test overlay only. Never print credentials or driver exceptions.
const {Client}=require("pg");
function validate(env){
  const u=new URL(env.DATABASE_READ_URL||"");
  if(!["postgresql:","postgres:"].includes(u.protocol)||u.hostname!=="postgres"||
    u.port!=="5432"||u.pathname!=="/ashare_test"||u.username!=="screener_reader"||
    !u.password||u.search||u.hash)throw Error("test reader URL rejected");
  if(env.DATABASE_URL||Object.keys(env).some(k=>/^(NEON_|TUSHARE_|AWS_)/.test(k)&&env[k]))
    throw Error("unexpected credentials");
  if(env.DATABASE_DRIVER!=="postgres"||env.ARCHIVE_STORAGE_DRIVER!=="local"||
    env.ARCHIVE_LOCAL_ROOT!=="/data/archive")throw Error("test drivers rejected");
  if(Array.from(env.SITE_ACCESS_PASSWORD||"").length<6||
    (env.SITE_ACCESS_PASSWORD||"").length>512||
    !/^[a-fA-F0-9]{64}$/.test(env.SITE_SESSION_SECRET||""))throw Error("password gate not configured");
}
async function main(){
  validate(process.env);
  const c=new Client({connectionString:process.env.DATABASE_READ_URL,connectionTimeoutMillis:5000,
    statement_timeout:5000,options:"-c default_transaction_read_only=on"});
  try{
    await c.connect();
    const {rows:[r]}=await c.query("SELECT current_database() AS db, current_user AS role, current_setting('transaction_read_only') AS ro, has_table_privilege(current_user,'public.stocks','SELECT') AS can_read, has_table_privilege(current_user,'public.stocks','INSERT,UPDATE,DELETE,TRUNCATE') AS can_write");
    if(r.db!=="ashare_test"||r.role!=="screener_reader"||r.ro!=="on"||!r.can_read||r.can_write)
      throw Error("database identity or privilege check failed");
    await c.query("SELECT 1 FROM public.stocks LIMIT 1");
    console.log("PASS: isolated test reader identity and query");
  }finally{await c.end();}
}
if(require.main===module)main().catch(()=>{console.error("Test Web guard rejected configuration or database");process.exitCode=1;});
module.exports={validate};
