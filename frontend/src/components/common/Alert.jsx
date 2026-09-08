import { AlertCircle, CheckCircle2, Info, AlertTriangle } from "lucide-react";

const icons = {
  error: AlertCircle,
  success: CheckCircle2,
  info: Info,
  warning: AlertTriangle,
};

export default function Alert({ type = "info", children, onClose }) {
  const Icon = icons[type] || Info;
  return (
    <div className={`alert alert-${type}`}>
      <Icon size={16} />
      <span className="alert-text">{children}</span>
      {onClose && (
        <button className="alert-close" onClick={onClose}>&times;</button>
      )}
    </div>
  );
}
