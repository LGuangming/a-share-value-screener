import StockComparison from "@/components/stock-comparison";
export const metadata={title:"股票对比"};
export default async function ComparePage({searchParams}:{searchParams:Promise<{codes?:string}>}){
  const {codes}=await searchParams;
  const selected=[...new Set((typeof codes==="string"?codes:"").split(",").filter(c=>/^\d{6}\.(SH|SZ|BJ)$/.test(c)))].slice(0,5);
  return <main className="page-container"><div className="page-top"><div><h1>股票对比</h1></div></div><StockComparison key={selected.join(",")} initialCodes={selected}/></main>;
}
