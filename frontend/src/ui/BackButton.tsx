import { useNavigate } from "react-router-dom";

/** Кнопка «назад» для не-табовых экранов (калькулятор, сравнение):
 *  пришли с карточки → возвращаемся в историю (PDP/лента). */
export function BackButton({ label = "Назад" }: { label?: string }) {
  const navigate = useNavigate();
  return (
    <button className="screen-back" aria-label={label} onClick={() => navigate(-1)}>
      ‹
    </button>
  );
}
