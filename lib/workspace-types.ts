import type {ScreenerRow} from "./data";
import type {CompanyOverview} from "./company-research";
export type StockIdentity={ts_code:string;name:string;industry?:string|null};
export type WorkspaceDetail={identity:StockIdentity;overview:CompanyOverview;stock:ScreenerRow|null;prices:{day:string;close:number}[];recent:ScreenerRow[]};
export type WorkspaceStock=StockIdentity & {hits?:{id:string;name:string}[]};
