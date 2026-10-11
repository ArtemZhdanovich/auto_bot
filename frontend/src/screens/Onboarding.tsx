/** Онбординг (ТЗ п. 9.2.1): приветствие + запрос имени через Telegram context. */

import { Button, Placeholder } from "@telegram-apps/telegram-ui";

import { tgUser } from "../api/auth";

export function OnboardingScreen({ onDone }: { onDone: () => void }) {
  const tg = tgUser();
  const name = tg?.first_name ?? "друг";

  return (
    <div style={{ padding: "24px" }}>
      <Placeholder
        header={`Привет, ${name}!`}
        description="Подбирайте автомобили из Кореи, Европы, США и Китая. Настройте фильтры — мы пришлём уведомление, когда появятся новые варианты."
        action={<Button size="l" stretched onClick={onDone}>Начать подбор</Button>}
      />
      <Placeholder description="🔍 Поиск · ❤️ Избранное · 🔔 Уведомления — всё в одном месте" />
    </div>
  );
}
