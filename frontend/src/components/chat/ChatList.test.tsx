/** Тесты списка тредов (ChatList): unread-бейдж, сниппет ≤60 симв.,
 *  поддержка фиксом сверху, выбор треда, subject-миниатюра; говернанс
 *  (08 §6/§7): muted-тред — 🔕 без счётчика, закрытые — секция «Архив». */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatList, plainSnippet, sortThreads } from "./ChatList";
import type { ChatThread } from "../../lib/chatApi";

const thread = (over: Partial<ChatThread> = {}): ChatThread => ({
  id: "t1",
  type: "seller",
  title: "Kia K5",
  status: "active",
  unread: 0,
  last_message: {
    body: "Машина ещё в продаже?",
    created_at: "2026-10-25T10:00:00Z",
    sender_type: "user",
  },
  subject: {
    listing_id: 7,
    make: "Kia",
    model: "K5",
    year: 2022,
    price: 18_400_000,
    currency: "KRW",
    image: "https://img/k5.jpg",
  },
  ...over,
});

afterEach(cleanup);

describe("ChatList", () => {
  it("строка треда: заголовок, сниппет, время, subject-миниатюра", () => {
    render(<ChatList threads={[thread()]} onSelect={vi.fn()} />);
    expect(screen.getByText("Kia K5")).toBeTruthy();
    expect(screen.getByText("Машина ещё в продаже?")).toBeTruthy();
    // миниатюра предмета (alt="" — декоративная, ищем по классу)
    expect(
      (document.querySelector(".chat-item__thumb") as HTMLImageElement)?.src,
    ).toBe("https://img/k5.jpg");
  });

  it("unread-бейдж показывает счётчик (99+ для больших), 0 — без бейджа", () => {
    render(
      <ChatList
        threads={[
          thread({ id: "a", unread: 3 }),
          thread({ id: "b", unread: 0 }),
          thread({ id: "c", unread: 150 }),
        ]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("3 непрочитанных").textContent).toBe("3");
    expect(screen.getByText("99+")).toBeTruthy(); // 150 схлопнуто до 99+
    expect(document.querySelectorAll(".chat-item__unread").length).toBe(2); // b — без бейджа
  });

  it("сниппет — плейн-текст не длиннее 60 символов с многоточием", () => {
    const long = "а".repeat(120);
    expect(plainSnippet(long)).toHaveLength(61); // 60 + «…»
    expect(plainSnippet(long).endsWith("…")).toBe(true);
    // markdown-разметка срезается
    expect(plainSnippet("**жирный** и `код` [ссылка](https://x.dev)")).toBe(
      "жирный и код ссылка",
    );
    render(
      <ChatList
        threads={[thread({ id: "long", last_message: { body: long, created_at: "2026-10-25T10:00:00Z", sender_type: "bot" } })]}
        onSelect={vi.fn()}
      />,
    );
    const snippet = document.querySelector(".chat-item__snippet")?.textContent ?? "";
    expect(snippet.length).toBeLessThanOrEqual(61);
  });

  it("поддержка фиксируется сверху, дальше — по свежести", () => {
    const support = thread({ id: "sup", type: "support", title: "Поддержка" });
    const older = thread({
      id: "old",
      title: "Старый тред",
      last_message: { body: "старое", created_at: "2026-10-01T10:00:00Z", sender_type: "user" },
    });
    const newer = thread({
      id: "new",
      title: "Новый тред",
      last_message: { body: "новое", created_at: "2026-10-24T10:00:00Z", sender_type: "user" },
    });
    render(<ChatList threads={[older, newer, support]} onSelect={vi.fn()} />);
    const titles = Array.from(document.querySelectorAll(".chat-item__title")).map(
      (el) => el.textContent,
    );
    // поддержка первой, свежий seller раньше старого
    expect(titles[0]).toBe("Поддержка");
    expect(titles.indexOf("Новый тред")).toBeLessThan(titles.indexOf("Старый тред"));
  });

  it("клик по строке отдаёт id треда; активный подсвечен", () => {
    const onSelect = vi.fn();
    render(<ChatList threads={[thread()]} activeId="t1" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: /Чат: Kia K5/ }));
    expect(onSelect).toHaveBeenCalledWith("t1");
    expect(document.querySelector(".chat-item--active")).not.toBeNull();
  });

  it("переговорная (group, 07 §2): бейдж «Переговорная», аватар 👥", () => {
    render(
      <ChatList
        threads={[
          thread({
            id: "g1",
            type: "group",
            title: "Осмотр в субботу",
            is_group: true,
            subject: null,
            participants: [
              { user_type: "user", user_id: 1, name: "Аня" },
              { user_type: "user", user_id: 2, name: "Борис" },
            ],
          }),
        ]}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("Осмотр в субботу")).toBeTruthy();
    expect(screen.getByText("Переговорная")).toBeTruthy();
    // пустой subject → аватар-заглушка с 👥 (не 🚗)
    expect(document.querySelector(".chat-item__thumb--empty")?.textContent).toBe("👥");
    expect(document.querySelector(".chat-item__type--group")).not.toBeNull();
  });

  it("muted-тред (08 §6): 🔕 вместо счётчика, unread не показывается", () => {
    render(
      <ChatList
        threads={[
          thread({ id: "m", unread: 5, muted: true, title: "Шумная" }),
          thread({ id: "u", unread: 3, title: "Обычный" }),
        ]}
        onSelect={vi.fn()}
      />,
    );
    expect(document.querySelector(".chat-item__muted")?.textContent).toBe("🔕");
    expect(screen.queryByLabelText("5 непрочитанных")).toBeNull();
    // соседний не-muted — со счётчиком
    expect(screen.getByLabelText("3 непрочитанных")).toBeTruthy();
  });

  it("закрытые треды (08 §7): свёрнутая секция «Архив» внизу, серые с меткой «архив»", () => {
    const onSelect = vi.fn();
    render(
      <ChatList
        threads={[
          thread({ id: "a1", title: "Активный" }),
          thread({ id: "c1", status: "closed", title: "Старый чат" }),
        ]}
        onSelect={onSelect}
      />,
    );
    const archive = screen.getByTestId("chat-archive");
    expect(archive.textContent).toContain("1"); // счётчик закрытых
    // свёрнуто: строки закрытого треда не видно, активный — на месте
    expect(screen.queryByRole("button", { name: /Чат: Старый чат/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Чат: Активный/ })).toBeTruthy();

    // раскрываем: строка серым, метка «архив» вместо типа
    fireEvent.click(screen.getByRole("button", { name: /Архив/ }));
    const row = screen.getByRole("button", { name: /Чат: Старый чат/ });
    expect(row.className).toContain("chat-item--archived");
    expect(screen.getByText("архив")).toBeTruthy();
    // открытие закрытого треда — тот же onSelect (комната будет read-only)
    fireEvent.click(row);
    expect(onSelect).toHaveBeenCalledWith("c1");
  });
});

describe("sortThreads", () => {
  it("пустой и одноэлементный списки не ломаются", () => {
    expect(sortThreads([])).toEqual([]);
    expect(sortThreads([thread()])).toHaveLength(1);
  });
});
