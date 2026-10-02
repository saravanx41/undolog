import {
  Calendar,
  ContactRound,
  CreditCard,
  Mail,
  StickyNote,
  Wrench,
  type LucideIcon,
} from "lucide-react";

const TOOL_ICONS: Record<string, LucideIcon> = {
  gmail: Mail,
  "gmail.send": Mail,
  stripe: CreditCard,
  "stripe.create_charge": CreditCard,
  crm: ContactRound,
  "crm.read_record": ContactRound,
  "crm.update_record": ContactRound,
  "crm.update_many": ContactRound,
  calendar: Calendar,
  "calendar.slot_hold": Calendar,
  notes: StickyNote,
  "notes.add": StickyNote,
};

export function ToolIcon({ tool, className }: { tool: string; className?: string }) {
  const Icon = TOOL_ICONS[tool] ?? Wrench;
  return <Icon className={className} />;
}
