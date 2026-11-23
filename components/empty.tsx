import { AlertCircle, DatabaseZap } from "lucide-react";

export function Empty({ title, detail, type="empty" }: { title: string; detail: string; type?: "empty" | "warning" }) {
  return <div className="empty">
    <div className="empty-icon">{type === "warning" ? <AlertCircle size={26}/> : <DatabaseZap size={28}/>}</div>
    <strong>{title}</strong><p>{detail}</p>
  </div>;
}
