import { Loader2 } from "lucide-react";

export default function Spinner({ size = 20, text }) {
  return (
    <div className="spinner-wrapper">
      <Loader2 size={size} className="spin" />
      {text && <span className="spinner-text">{text}</span>}
    </div>
  );
}
