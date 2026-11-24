import type {ScreenerRow} from "./data";
export type StockIdentity={ts_code:string;name:string;industry?:string|null};
export type WorkspaceDetail={identity:StockIdentity;stock:ScreenerRow|null;prices:{day:string;close:number}[];recent:ScreenerRow[]};
export type WorkspaceStock=StockIdentity & {hits?:{id:string;name:string}[]};
