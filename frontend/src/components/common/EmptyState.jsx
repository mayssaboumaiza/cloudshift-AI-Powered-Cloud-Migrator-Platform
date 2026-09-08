import { Inbox } from "lucide-react";

export default function EmptyState({ icon: Icon = Inbox, title, description, children }) {
  return (
    <div className="empty-state">
      <Icon size={48} strokeWidth={1} />
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}
