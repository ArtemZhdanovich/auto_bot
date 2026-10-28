/**
 * Tooltip (02-design-system.md §7.13) — подсказка, не ломающая вёрстку.
 *
 * Пузырь рендерится через портал в document.body и позиционируется
 * position: fixed по getBoundingClientRect() триггера — никогда
 * position: absolute внутри родителя: карточки и ряды имеют
 * overflow: hidden / скролл, absolute-пузырь режется и сдвигает layout.
 *
 * Показ — hover/focus-visible с задержкой (по умолчанию 300мс, дока §7.13),
 * появление — fade 120мс (--dur-fast); на touch — по тапу, повторный тап
 * или тап вне — закрывают. Esc закрывает, скролл/resize — скрывают
 * (координаты устарели). Связь с триггером — aria-describedby.
 * max-width 240px + перенос слов: длинный VIN не распирает экран, по X
 * пузырь clamped в вьюпорт с отступом 8px. Работает в обеих темах
 * (светлая — инверсия text/bg, тёмная — surface + бордер, см. index.css).
 */

import {
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

interface TooltipProps {
  /** Текст подсказки (переносится по словам, ширина ≤ 240px) */
  text: string;
  /** Триггер: элемент (к нему прокидывается aria-describedby) или текст */
  children: ReactNode;
  /** Задержка показа при hover/focus, мс (§7.13: 300) */
  delay?: number;
}

/** Отступ пузыря от края вьюпорта при clamp по X/Y (§7.13.2) */
const VIEWPORT_MARGIN = 8;
/** Зазор между пузырём и триггером */
const TRIGGER_GAP = 8;

export function Tooltip({ text, children, delay = 300 }: TooltipProps) {
  const id = useId();
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const bubbleRef = useRef<HTMLDivElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const [visible, setVisible] = useState(false);
  /** null = пузырь ещё не спозиционирован (скрыт, чтобы не мигал в углу) */
  const [position, setPosition] = useState<CSSProperties | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const hide = useCallback(() => {
    clearTimer();
    setVisible(false);
    setPosition(null);
  }, [clearTimer]);

  const show = useCallback(
    (openDelay: number) => {
      clearTimer();
      timerRef.current = window.setTimeout(() => setVisible(true), openDelay);
    },
    [clearTimer],
  );

  // Таймер не должен выстрелить после размонтирования триггера
  useEffect(() => clearTimer, [clearTimer]);

  // Позиционирование (§7.13.2): по умолчанию сверху, при нехватке места —
  // flip вниз; по X — центр над триггером с clamp в вьюпорт (отступ 8px).
  useLayoutEffect(() => {
    if (!visible || !triggerRef.current || !bubbleRef.current) return;
    const trigger = triggerRef.current.getBoundingClientRect();
    const bubble = bubbleRef.current.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    let top = trigger.top - bubble.height - TRIGGER_GAP;
    if (top < VIEWPORT_MARGIN) {
      top = trigger.bottom + TRIGGER_GAP; // сверху не помещается — flip
    }
    // clamp по вертикали на низких вьюпортах
    top = Math.min(
      Math.max(top, VIEWPORT_MARGIN),
      Math.max(VIEWPORT_MARGIN, vh - bubble.height - VIEWPORT_MARGIN),
    );
    const left = Math.min(
      Math.max(trigger.left + (trigger.width - bubble.width) / 2, VIEWPORT_MARGIN),
      Math.max(VIEWPORT_MARGIN, vw - bubble.width - VIEWPORT_MARGIN),
    );

    setPosition({ top, left });
  }, [visible, text]);

  // Скролл/resize: координаты устарели — скрываем (§7.13.3).
  // capture=true: скролл внутри вложенных контейнеров тоже ловим.
  useEffect(() => {
    if (!visible) return;
    window.addEventListener("scroll", hide, true);
    window.addEventListener("resize", hide);
    return () => {
      window.removeEventListener("scroll", hide, true);
      window.removeEventListener("resize", hide);
    };
  }, [visible, hide]);

  // touch: тап вне триггера закрывает подсказку
  useEffect(() => {
    if (!visible) return;
    const onDocPointerDown = (e: PointerEvent) => {
      if (triggerRef.current?.contains(e.target as Node)) return;
      hide();
    };
    document.addEventListener("pointerdown", onDocPointerDown);
    return () => document.removeEventListener("pointerdown", onDocPointerDown);
  }, [visible, hide]);

  const isElement = isValidElement(children);
  // aria-describedby — на самом триггере (§7.13.3): элементу прокидываем
  // атрибут клонированием (его пропсы сохраняются); для текстового триггера
  // атрибут остаётся на span-обёртке, она же становится focusable.
  const trigger = isElement
    ? cloneElement(children as ReactElement<{ "aria-describedby"?: string }>, {
        "aria-describedby": id,
      })
    : children;

  return (
    <span
      ref={triggerRef}
      className="tooltip-trigger"
      aria-describedby={isElement ? undefined : id}
      tabIndex={isElement ? undefined : 0}
      onMouseEnter={() => show(delay)}
      onMouseLeave={hide}
      onFocus={() => show(delay)}
      onBlur={hide}
      onKeyDown={(e) => {
        if (e.key === "Escape") hide();
      }}
      onPointerDown={(e) => {
        // Мышь управляется hover'ом; touch/pen — тап: показать/скрыть
        if (e.pointerType === "mouse") return;
        if (visible) hide();
        else show(0);
      }}
    >
      {trigger}
      {visible
        ? createPortal(
            <div
              ref={bubbleRef}
              id={id}
              role="tooltip"
              className="tooltip"
              style={position ?? { visibility: "hidden" }}
            >
              {text}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
