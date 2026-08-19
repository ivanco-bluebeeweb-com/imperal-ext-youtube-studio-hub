# Post-Audit Log — Youtube Studio

Формат и правила ведения: см. `/Users/vladivanco/Documents/Imperal OS/POST_AUDIT_LOG_STANDARD.md`.
Новые записи добавляются СВЕРХУ.

---

## 2026-08-19 — Plausible Scenario Testing (PST) — реальный баг найден и исправлен

Метод и детали в `SCENARIO_TESTS.md`. Все 25 `@chat.function` уже имели
покрытие "по имени" (вызывались хотя бы раз в существующих тестах), но
единственный тест `disconnect_account` проверял только удаление самой
записи аккаунта, никогда не проверял побочную очистку данных. Составлен
`tests/test_pst_coverage.py` (5 сценариев: жизненный цикл идей, изоляция
между аккаунтами, аккаунт без закешированных channel_ids, ошибка на
несуществующем id, AI-сгенерированные идеи).

**Найден и исправлен реальный баг: `disconnect_account` никогда не
удалял сохранённые идеи аккаунта.** `accounts.disconnect()` искал
`youtube_content_ideas` по `where={"account_email": email}`, но ни
`save_content_idea`, ни `generate_content_ideas` никогда не пишут поле
`account_email` на документ идеи — запрос всегда возвращал 0 строк.
Функция открыто обещает в своём описании: "Its local YouTube Studio Hub
data (saved ideas) is removed" — но идеи оставались в хранилище
навсегда. Исправлено: очистка теперь ищет идеи через
`channel_id ∈ account.channel_ids` (тот же join, что уже использует
`account_for_channel()`), а не через несуществующее поле.

Итог: 5/5 новых PST-тестов зелёные, полный набор 34/34 (было 29). Код
приложения изменён (`accounts.py`) — коммит + push, `deploy_app` для
подтверждения.

---

## 2026-08-19 — Сквозной пост-аудит + исправление double-prompt бага в delete_idea

**Что проверялось:** py_compile всех 8 модулей; количество `@chat.function`
(25, совпадает с манифестом); классификация `action_type` каждой функции,
особое внимание единственной функции с именем `delete_*` (`delete_idea`);
double-prompt антипаттерн (ручной `confirm` в UI-панели рядом с
классификацией, которая должна быть `destructive`); полный прогон тестов
(`tests/`, 29 тестов через `.venv/bin/pytest`).

**Метод:** распечатала полный список `name -> action_type` из
`imperal.json`; прочитала код `delete_idea` в `handlers.py` (прямой
`ctx.store.delete`, никакого trash/restore пути — то есть безвозвратно);
grep по всем `*.py` на `confirm` нашёл РЕАЛЬНОЕ совпадение в `panels.py`:
кнопка "Delete" у карточки идеи имела ручное `"confirm": "Delete this
content idea?"` в UI, при этом сама функция была классифицирована
`action_type=\"write\"`, а не `\"destructive\"`.

### Находки

1. **Реальный баг: `delete_idea` был `action_type=\"write\"` + ручной UI
   confirm — double-prompt/misclassification, тот же паттерн, что найден
   ранее в этой серии аудитов (Media Studio, Trello Connector).**
   Описание манифеста ("Permanently delete a saved content idea") и код
   (прямой `ctx.store.delete`, без восстановления) однозначно указывают на
   безвозвратную операцию — должна быть `destructive`, чтобы платформенный
   гейт подтверждения сработал сам, без ручного дублирования в UI.
2. Единственный тест, вызывающий `delete_idea`
   (`tests/test_smoke.py:279`), вызывает функцию напрямую и не затронут
   изменением `action_type`/UI — прошёл без изменений.

### Что сделано

1. `handlers.py`: `delete_idea` — `action_type` изменён `write` →
   `destructive`, добавлено объяснение в docstring по образцу уже
   исправленных ранее функций в других приложениях.
2. `panels.py`: убрано ручное поле `"confirm": "Delete this content idea?"`
   у кнопки удаления идеи — гейт теперь только платформенный.
3. `imperal.json`: синхронизирован скриптом (`delete_idea` → `destructive`).
4. Проверено: `python3 -m py_compile` — чисто; JSON валиден; полный
   тестовый набор (29 тестов) — все прошли.

**Статус: было 1 несоответствие (double-prompt/misclassification),
исправлено.**
