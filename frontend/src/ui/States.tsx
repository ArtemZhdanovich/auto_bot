/** Empty / Error states (UI/UX ТЗ §2.2). */

import { Button } from "@telegram-apps/telegram-ui";

function CarIcon({ size = 48 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2" />
      <circle cx="7" cy="17" r="2" />
      <path d="M9 17h6" />
      <circle cx="17" cy="17" r="2" />
    </svg>
  );
}

export function EmptyState({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="state">
      <CarIcon />
      <div>
        <div className="state__title">{title}</div>
        {description && <div>{description}</div>}
      </div>
      {actionLabel && onAction && (
        <Button size="m" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}

export function ErrorState({
  description,
  onRetry,
}: {
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state">
      <CarIcon />
      <div>
        <div className="state__title">Что-то пошло не так</div>
        {description && <div>{description}</div>}
      </div>
      {onRetry && (
        <Button size="m" mode="outline" onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}
