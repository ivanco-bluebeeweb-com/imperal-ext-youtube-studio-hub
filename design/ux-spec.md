# YouTube Studio Hub — UX Specification (Фаза 3, до кода)

**Статус:** approved — точная спека получена от пользователя дословно,
формализована ниже как компонентное дерево. Следующий шаг после этого
документа — реализация (`panels.py`).

**Обязательные правила, которые ЭТА спека уже соблюдает** (проверено против
`UI_INTERFACE_STANDARD.md` и накопленных правил сессии):
- РОВНО одна кнопка "App Settings" в сайдбаре.
- Никаких `ui.Card`-обёрток с паддингом в сайдбаре — блоки разделены
  `ui.Divider`, элементы — голые `ui.Button`/`ui.Select`/`ui.List`.
- `ui.Tabs` не используется (unproven в живых панелях экосистемы) — вместо
  этого проверенный паттерн `ui.Button` + `ui.Call(tab=...)`, как в Brand
  Strategy Hub.
- Диалог подключения (`ui.Dialog`) — сама платформа НЕ умеет ограничивать
  ширину `ui.Dialog`/`center_overlay`-панелей (см. зафиксированный баг
  платформы в заметке «Баг/недоработка: у center-overlay диалогов нет
  ограничения ширины»). Мы используем `ui.Dialog` как просила пользователь,
  но не можем сузить его — это известное ограничение, не наша недоработка.

---

## 1. Левый сайдбар (сверху вниз, дословно по ТЗ)

```
┌───────────────────────────────────────────┐
│  [ + Connect Google Account ]              │  ← primary CTA-кнопка (full-width)
│                                             │     on_click → открывает ui.Dialog
│                                             │       (OAuth: "Continue with Google"
│                                             │        ссылка на ctx.oauth_authorize_url)
├── ui.Divider ───────────────────────────── ┤
│  Channel: [ ksrenovationgroup ▾ ]          │  ← ui.Select — выбор ПОДКЛЮЧЁННОГО канала
│                                             │     (все каналы всех подключённых
│                                             │      Google-аккаунтов в одном списке)
├── ui.Divider ───────────────────────────── ┤
│  [ App Settings ]                          │  ← secondary-кнопка, ровно одна
├── ui.Divider ───────────────────────────── ┤
│  ◉ KS Renovation Group          [avatar]   │  ← кликабельный ui.ListItem
│  ◉ G4S Moldova                  [avatar]   │     avatar = ui.Avatar(src=channel
│  ◉ Climtec                      [avatar]   │     thumbnail), title = channel name
│                                             │     on_click → ui.Call(view="channel",
│                                             │                        channel_id=...)
└─────────────────────────────────────────────┘
```

Пояснения по механике:
- Пока НЕТ ни одного подключённого Google-аккаунта: рендерится **только**
  CTA-кнопка "Connect Google Account" — ничего лишнего не показываем раньше
  времени (тот же принцип connect-first, что в Aidentika/Page Speed
  Insights/Google Drive Connector).
- `ui.Select` "Channel" — быстрый переключатель активного канала без
  прокрутки списка внизу; синхронизирован с кликом по списку (оба меняют
  один и тот же `channel_id` в состоянии панели).
- Список каналов внизу — ВСЕ каналы со ВСЕХ подключённых аккаунтов вместе,
  без группировки по аккаунту в P0 (если каналов станет много — можно
  добавить `subtitle` с email аккаунта на `ListItem`, не блокер сейчас).

---

## 2. Центр — пустое состояние (ничего не выбрано)

```
┌─────────────────────────────────────────────────────┐
│                                                       │
│              (ui.Empty)                              │
│      "Select a channel to see its content,           │
│       analytics and ideas."                          │
│                                                       │
└─────────────────────────────────────────────────────┘
```

---

## 3. Центр — Channel Detail (после клика по каналу)

```
┌─────────────────────────────────────────────────────┐
│  KS Renovation Group                                 │  ← ui.Header (channel title)
│  youtube.com/@ksrenovationgroup                      │  ← ui.Link (external, opens channel)
├───────────────────────────────────────────────────── ┤
│ [My Content (24)] Analytics  Channel Management  Content Ideas │  ← таб-ряд
│  ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔                                     │     (Button+ui.Call(tab=...),
│                                                       │      активный таб подчёркнут/
│                                                       │      variant="primary")
├───────────────────────────────────────────────────── ┤
│  [ tab content — see 3.1 / 3.2 / 3.3 / 3.4 below ]   │
└─────────────────────────────────────────────────────┘
```

Порядок табов (дословно по ТЗ): **My Content (X) → Analytics →
Channel Management → Content Ideas**. По умолчанию активен **My Content**.
`(X)` = число видео на канале, как счётчик — тот же паттерн, что
`ArticleBrief`/`_status_label` показывают счётчики в других приложениях.

### 3.1 Tab: My Content (X) — default

```
  [ Filter: All ▾ ]  [ Sort: Newest ▾ ]        (опционально, не блокер P0)
  ┌───────────────────────────────────────┐
  │ [thumb] How to fix a leaking faucet    │  ← ui.ListItem, on_click меняет
  │         12,4K views · 3 days ago        │     центр на Video Detail (§4)
  ├───────────────────────────────────────┤
  │ [thumb] 5 signs your roof needs repair │
  │         8,1K views · 1 week ago         │
  ├───────────────────────────────────────┤
  │ ...                                     │
  └───────────────────────────────────────┘
```
Источник данных: `videos.list` (Data API v3, `mine=true` через
`search.list` + `videos.list` для деталей) по каналу.

### 3.2 Tab: Analytics (channel-level)

```
  Overview (последние 28 дней, по умолчанию)
  ┌──────────┬──────────┬──────────┬──────────┐
  │ Views    │ Watch    │ Subs     │ Est.     │  ← ui.Stats
  │ 42.1K    │ time(h)  │ +214     │ Revenue  │
  │          │  1,204   │          │  n/a*    │
  └──────────┴──────────┴──────────┴──────────┘
  Top videos by views (эта же таблица, отсортирована)
  Traffic sources (chart/список)
  Audience retention (усреднённая, если доступно)
```
`* Est. Revenue` — только если `yt-analytics-monetary.readonly` scope
согласован явно (см. PREPARATION.md §7) — иначе прочерк, не выдумываем.
Источник: YouTube Analytics API v2 `reports.query`.

### 3.3 Tab: Channel Management

```
  Playlists                                    [+ New playlist]
  ┌───────────────────────────────────────┐
  │ Home renovation tips (12 videos)       │
  │ Client testimonials (5 videos)         │
  └───────────────────────────────────────┘
  ─────────────────────────────────────────
  Comments needing attention (held/flagged)
  ┌───────────────────────────────────────┐
  │ "Great video!" — @user123   [Reply]    │
  └───────────────────────────────────────┘
  ─────────────────────────────────────────
  Channel branding (banner, description, links) — read + edit
```
Источники: `playlists.list`/`playlistItems`, `commentThreads.list`,
`channels.list(part=brandingSettings)`.

### 3.4 Tab: Content Ideas

```
  [ Generate ideas from keyword signals ]     (кнопка, аналог
                                                discover_opportunities)
  ┌───────────────────────────────────────┐
  │ "How to unclog a drain without         │
  │  chemicals" — search volume: high,     │
  │  competition: low            [Create brief]│
  └───────────────────────────────────────┘
```
Тот же контракт, что `discover_opportunities` в Content Strategy Hub:
приложение НЕ дергает DataForSEO само — Webbee передаёт query-сигналы
явным параметром, приложение только скорит/кластеризует.

---

## 4. Центр — Video Detail (после клика по видео в My Content)

```
┌─────────────────────────────────────────────────────┐
│  ← Back to KS Renovation Group                       │  ← ui.Link/Button back
│  How to fix a leaking faucet                         │  ← ui.Header
│  [thumbnail]  12,4K views · 3 days ago · Public       │
├───────────────────────────────────────────────────── ┤
│  Metadata (edit):                                     │
│   Title        [___________________]                 │
│   Description  [___________________]                 │
│   Tags         [___________________]                 │
│   Thumbnail    [current] [Upload new]                 │
│   Category     [Select ▾]                             │
│   Visibility   [Public ▾]                              │
│                                       [Save changes]   │
├───────────────────────────────────────────────────── ┤
│  Video analytics: views/retention/CTR graph           │
├───────────────────────────────────────────────────── ┤
│  Comments on this video (list + reply/moderate)        │
└─────────────────────────────────────────────────────┘
```
Это единственное место, где происходит запись (`videos.update`,
`thumbnails.set`) — метаданные, НЕ монтаж/обрезка файла (граница из
PREPARATION.md §5, подтверждена пользователем явно).

---

## 5. App Settings (center_overlay, открывается кнопкой из сайдбара)

```
┌─────────────────────────────────────────────────────┐
│  App Settings                                        │
├───────────────────────────────────────────────────── │
│  Connected accounts                                   │
│   user@gmail.com          [Disconnect]                │
│   agency@gmail.com        [Disconnect]                │
├───────────────────────────────────────────────────── │
│  Default analytics period   [28 days ▾]               │
│  Default channel on open    [KS Renovation Group ▾]   │
└─────────────────────────────────────────────────────┘
```
Один экран, все настройки сразу — по правилу "ровно одна кнопка App
Settings, рендерит всё настраиваемое одним центральным экраном" (тот же
паттерн, что Aidentika/Page Speed Insights).

---

## 6. Открытые пункты дизайна (не блокеры P0, фиксирую честно)

1. Content Ideas в P0 — только приём внешних query-сигналов (как
   `discover_opportunities`); собственный keyword-research внутри
   YouTube Studio Hub не входит в P0.
2. Filter/Sort в My Content — опциональны для первого среза, список без
   них тоже полностью рабочий.
3. Monetary revenue в Analytics — зависит от согласования
   `yt-analytics-monetary.readonly` scope (см. PREPARATION.md), иначе
   поле просто не рендерится (не заглушка "n/a", а честное отсутствие
   блока).
