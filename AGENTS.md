# AGENTS.md — Operational Rules for AI Sessions

## Golden Rule
**Read PROJECT_CONTEXT.md first.** It contains the actual architecture. Do not assume.

## Context/Token Minimization
- **Inspect only relevant files** — use `grep`/`glob` to find the exact module before reading
- **Don't scan the whole repo** — targeted reads only
- **Keep responses concise** — 1-3 sentences unless user asks for detail

## Code Changes
- **Make minimal targeted changes** — fix the specific issue, don't rewrite working code
- **Follow existing conventions** — mimic code style, imports, patterns in the same file
- **Never expose/hardcode secrets** — use `config.X` only; secrets live in env vars
- **Never invent nutrition values** — all calories/macros must come from `fooddb.py` (local DB or FatSecret)
- **Use actual serving/portion logic** — respect per-100g vs per-serving distinction, gram_mode, qty extraction

## Workflow: FIND → FIX → TEST → VERIFY
1. **FIND**: Locate the exact code (grep for function, trace call chain)
2. **FIX**: Minimal edit following file conventions
3. **TEST**: Run targeted test (`python -m pytest tests/test_X.py::TestClass::test_method`)
4. **VERIFY**: Run lint/typecheck if available (check `requirements.txt` for tools)

## Work in Phases
- Break multi-step tasks into discrete phases
- Complete and verify each phase before moving on
- Don't modify unrelated features in the same change

## Regression Testing
- **Add regression tests** for any bug fix
- Place in appropriate `tests/test_*.py` matching the module
- Follow existing test patterns (see `tests/test_fooddb.py` for serving logic examples)

## Key Modules Reference
| Task | Primary File | Key Functions |
|------|-------------|---------------|
| Food parsing | `fooddb.py` | `parse_food`, `parse_local`, `parse_fatsecret`, `_extract_qty`, `_calc_nutrition` |
| Parser orchestration | `parser.py` | `parse`, `parse_food`, `reparse_food_with_answer`, `_shape_food` |
| Storage/Analytics | `storage.py` | `save_food`, `today_data`, `analytics`, `weekly_summary`, `export_csv` |
| Goals/Targets | `goals.py` | `calculate`, `current_targets`, `save_goal`, `latest_weight` |
| Portion learning | `portions.py` | `remember`, `hint_for`, `_key` |
| Config/Env | `config.py` | `validate`, all `os.getenv` vars |
| DB layer | `db.py` | `connect`, `_Conn`, `_Cursor` |

## Common Pitfalls to Avoid
- ❌ Don't add calories/macros directly in `parser.py` — call `fooddb.parse_food()`
- ❌ Don't change serving calculation without updating tests in `test_fooddb.py`
- ❌ Don't initialize DB at module level — use `@app.before_request` lazy init
- ❌ Don't commit `.env` or hardcode API keys
- ❌ Don't assume Gemini returns nutrition — it returns 0; DB lookup is mandatory
- ❌ Don't break multi-item parsing (`,`, `+`, `and`, `with` splits)
- ❌ Don't change audit trail fields (`source`, `matched_food`, `serving_g`, `qty`)

## Test Commands
```bash
# Run all tests
python -m pytest tests/ -v

# Run specific module
python -m pytest tests/test_fooddb.py -v
python -m pytest tests/test_parser.py -v
python -m pytest tests/test_goals.py -v
python -m pytest tests/test_storage.py -v

# Run single test
python -m pytest tests/test_fooddb.py::TestFoodDB::test_two_boiled_eggs_plus_two_chapati -v
```

## When Unsure
- Search the codebase first (`grep` for function names, patterns)
- Read the relevant test file — it documents expected behavior
- Ask the user for clarification rather than guessing