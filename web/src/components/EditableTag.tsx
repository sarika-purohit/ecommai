import type { ReactNode } from "react";
import { CloseIcon } from "@/components/icons";
import { Tag, type TagTone } from "@/components/Tag";
import { strings } from "@/lib/strings";

interface EditableTagProps {
  label: string;
  /** Lets focus come back to this tag after its editor closes. */
  editKey: string;
  tone?: TagTone;
  icon?: ReactNode;
  disabled?: boolean;
  onEdit?: () => void;
  onRemove?: () => void;
}

export function EditableTag({ label, editKey, tone, icon, disabled, onEdit, onRemove }: EditableTagProps) {
  return (
    <Tag tone={tone} icon={icon}>
      {onEdit ? (
        <button
          type="button"
          className="tag-text"
          data-edit-key={editKey}
          onClick={onEdit}
          disabled={disabled}
          aria-label={strings.understood.edit(label)}
        >
          {label}
        </button>
      ) : (
        <span className="tag-text">{label}</span>
      )}
      {onRemove ? (
        <button
          type="button"
          className="tag-action"
          onClick={onRemove}
          disabled={disabled}
          aria-label={strings.understood.remove(label)}
        >
          <CloseIcon className="size-5" />
        </button>
      ) : null}
    </Tag>
  );
}

export function focusEditKey(editKey: string) {
  window.setTimeout(() => {
    document.querySelector<HTMLElement>(`[data-edit-key="${CSS.escape(editKey)}"]`)?.focus();
  }, 0);
}
