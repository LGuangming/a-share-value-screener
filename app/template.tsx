/** Per-route wrapper remounts on navigation, activating the lightweight transition. */
export default function Template({children}:{children:React.ReactNode}){
  return <div className="route-transition">{children}</div>;
}
