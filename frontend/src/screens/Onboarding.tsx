/** Онбординг 3 шага (UI/UX ТЗ §2.8): приветствие → роль → страны. */

import { useState } from "react";
import { Button } from "@telegram-apps/telegram-ui";

import { tgUser } from "../api/auth";

export const COUNTRIES = ["Корея", "Китай", "Европа", "США", "Япония"] as const;

const ROLES = [
  { key: "buyer", title: "Покупатель", sub: "Ищу авто для себя" },
  { key: "dealer", title: "Перекуп", sub: "Ищу авто под перепродажу" },
  { key: "salon", title: "Салон-брокер", sub: "Работаю с клиентами" },
] as const;

export function OnboardingScreen({ onDone }: { onDone: (data: { role: string; countries: string[] }) => void }) {
  const tg = tgUser();
  const name = tg?.first_name ?? "друг";
  const [step, setStep] = useState(0);
  const [role, setRole] = useState<string | null>(null);
  const [countries, setCountries] = useState<string[]>([]);

  const toggleCountry = (c: string) =>
    setCountries((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]));

  return (
    <div className="onboarding">
      <div className="onboarding__dots">
        {[0, 1, 2].map((i) => (
          <span key={i} className={i === step ? "onboarding__dot onboarding__dot--active" : "onboarding__dot"} />
        ))}
      </div>

      {step === 0 && (
        <>
          <div>
            <h2 style={{ margin: "0 0 8px" }}>Привет, {name}!</h2>
            <p style={{ color: "var(--text-secondary)", lineHeight: 1.5 }}>
              Подбираем автомобили из Кореи, Китая, Европы, США и Японии.
              Настроите фильтры — пришлём уведомление, когда появятся новые варианты.
            </p>
          </div>
          <div style={{ color: "var(--text-secondary)", fontSize: 14 }}>
            🔎 Поиск · ❤️ Избранное · 🔔 Уведомления — всё в одном месте
          </div>
          <Button size="l" stretched onClick={() => setStep(1)}>
            Начать
          </Button>
        </>
      )}

      {step === 1 && (
        <>
          <h2 style={{ margin: 0 }}>Кто вы?</h2>
          <div className="onboarding__roles">
            {ROLES.map((r) => (
              <button
                key={r.key}
                className={role === r.key ? "onboarding__role onboarding__role--active" : "onboarding__role"}
                onClick={() => setRole(r.key)}
              >
                {r.title}
                <span className="onboarding__role-sub">{r.sub}</span>
              </button>
            ))}
          </div>
          <Button size="l" stretched disabled={!role} onClick={() => setStep(2)}>
            Далее
          </Button>
        </>
      )}

      {step === 2 && (
        <>
          <h2 style={{ margin: 0 }}>Какие страны интересуют?</h2>
          <div className="onboarding__countries">
            {COUNTRIES.map((c) => (
              <button
                key={c}
                className={countries.includes(c) ? "chip chip--active" : "chip"}
                style={{ padding: "12px" }}
                onClick={() => toggleCountry(c)}
              >
                {c}
              </button>
            ))}
          </div>
          <Button
            size="l"
            stretched
            onClick={() => onDone({ role: role ?? "buyer", countries })}
          >
            Готово
          </Button>
        </>
      )}
    </div>
  );
}

