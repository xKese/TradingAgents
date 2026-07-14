export default function Unavail({ msg }: { msg: string }) {
  return (
    <div className="unavail">
      <span className="tag">UNAVAIL</span>
      <span className="msg" title={msg}>{msg}</span>
    </div>
  );
}
