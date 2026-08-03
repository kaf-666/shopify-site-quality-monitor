import time

from playwright_checks.core.config_loader import locator
from playwright_checks.core.test_results import add_result
from playwright_checks.utils.capture import wait_for_layout_stable
from playwright_checks.utils.visual import build_result
from playwright_checks.utils.waits import build_paths, locate_element, selector_for


def _configured_locator(config, key):
    value = (config or {}).get(key)
    return locator(value) if value else None


def _safe_click(element, label):
    state = element.evaluate(
        """
        (element) => {
            const tag = element.tagName.toLowerCase();
            const type = String(element.getAttribute('type') || '').toLowerCase();
            const form = element.closest('form');
            const href = String(element.getAttribute('href') || '').trim();
            return {
                tag,
                type,
                href,
                formAction: form ? String(form.action || '') : '',
            };
        }
        """
    )
    if state.get("type") == "submit" or state.get("formAction"):
        raise AssertionError(f"{label} is a form submission control")
    href = state.get("href", "")
    if state.get("tag") == "a" and href and not href.startswith("#"):
        raise AssertionError(f"{label} would navigate to {href!r}")
    element.click(timeout=10000)


def _panel_state(page, panel, close, bottom_action=None):
    return panel.evaluate(
        """
        (panel, options) => {
            const rect = panel.getBoundingClientRect();
            const style = window.getComputedStyle(panel);
            const root = document.documentElement;
            const closeRect = options.close
                ? options.close.getBoundingClientRect()
                : null;
            const bottomRect = options.bottomAction
                ? options.bottomAction.getBoundingClientRect()
                : null;
            const pointVisible = (target, box) => {
                if (!target || !box || box.width <= 0 || box.height <= 0) {
                    return false;
                }
                const x = Math.max(0, Math.min(
                    window.innerWidth - 1,
                    box.left + box.width / 2
                ));
                const y = Math.max(0, Math.min(
                    window.innerHeight - 1,
                    box.top + box.height / 2
                ));
                const hit = document.elementFromPoint(x, y);
                return Boolean(hit && (hit === target || target.contains(hit)));
            };
            return {
                panelVisible:
                    rect.width > 0
                    && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden',
                rect: {
                    left: rect.left,
                    top: rect.top,
                    right: rect.right,
                    bottom: rect.bottom,
                    width: rect.width,
                    height: rect.height,
                },
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
                withinViewport:
                    rect.left >= -2
                    && rect.top >= -2
                    && rect.right <= window.innerWidth + 2
                    && rect.bottom <= window.innerHeight + 2,
                pageHorizontalOverflow:
                    root.scrollWidth > root.clientWidth + 2,
                panelHorizontalOverflow:
                    panel.scrollWidth > panel.clientWidth + 2
                    && !['auto', 'scroll'].includes(style.overflowX),
                closeVisible: pointVisible(options.close, closeRect),
                bottomActionVisible: options.bottomAction
                    ? pointVisible(options.bottomAction, bottomRect)
                        && bottomRect.bottom <= window.innerHeight + 2
                    : true,
            };
        }
        """,
        {
            "close": close.element_handle(),
            "bottomAction": (
                bottom_action.element_handle() if bottom_action else None
            ),
        },
    )


def _element_is_visible(element):
    try:
        return bool(element.is_visible(timeout=500))
    except Exception:
        return False


def _page_scroll_state(page):
    return page.evaluate(
        """
        () => {
            const body = window.getComputedStyle(document.body);
            const root = window.getComputedStyle(document.documentElement);
            const lockedValues = new Set(['hidden', 'clip']);
            return {
                scrollY: window.scrollY,
                maxScrollY: Math.max(
                    0,
                    document.documentElement.scrollHeight - window.innerHeight
                ),
                bodyOverflow: body.overflow,
                bodyOverflowY: body.overflowY,
                rootOverflow: root.overflow,
                rootOverflowY: root.overflowY,
                locked:
                    body.position === 'fixed'
                    || lockedValues.has(body.overflow)
                    || lockedValues.has(body.overflowY)
                    || lockedValues.has(root.overflow)
                    || lockedValues.has(root.overflowY),
            };
        }
        """
    )


def _probe_scroll_restored(page):
    return page.evaluate(
        """
        () => {
            const root = document.documentElement;
            const startY = window.scrollY;
            const maxY = Math.max(0, root.scrollHeight - window.innerHeight);
            if (maxY <= 0) {
                return {
                    scrollable: false,
                    moved: true,
                    restored: true,
                };
            }
            const previousBehavior = root.style.scrollBehavior;
            root.style.scrollBehavior = 'auto';
            const targetY = startY < maxY
                ? Math.min(maxY, startY + 8)
                : Math.max(0, startY - 8);
            window.scrollTo(0, targetY);
            const moved = Math.abs(window.scrollY - startY) >= 1;
            window.scrollTo(0, startY);
            const restored = Math.abs(window.scrollY - startY) < 1;
            root.style.scrollBehavior = previousBehavior;
            return {
                scrollable: true,
                moved,
                restored,
            };
        }
        """
    )


def _record_skip(ctx, case_name, reason):
    policy = ctx.screenshot_policy(case_name)
    print(f"SKIP [{case_name}] {reason}")
    add_result(
        build_result(
            ctx.site,
            ctx.suite,
            ctx.page_name,
            policy["report_case"],
            "skipped",
            None,
            details={
                "screenshot_purpose": policy["purpose"],
                "skip_reason": reason,
                "affects_exit_code": False,
            },
        )
    )


def capture_readonly_panel(ctx, page, interaction_name, case_name):
    policy = ctx.screenshot_policy(case_name)
    if not policy["enabled"]:
        return {}, []
    config = (
        (ctx.page_config.get("readonly_interactions") or {}).get(
            interaction_name
        )
        or {}
    )
    if not config:
        skip_reason = (
            ctx.page_config.get("readonly_interaction_skip_reasons") or {}
        ).get(interaction_name)
        _record_skip(
            ctx,
            case_name,
            skip_reason or "selector_not_configured",
        )
        return {}, []

    trigger_locator = _configured_locator(config, "trigger")
    panel_locator = _configured_locator(config, "panel")
    close_locator = _configured_locator(config, "close")
    bottom_locator = _configured_locator(config, "bottom_action")
    dismiss_locator = _configured_locator(config, "dismiss_obstruction")
    if not trigger_locator or not panel_locator or not close_locator:
        _record_skip(ctx, case_name, "incomplete_selector_config")
        return {}, []

    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        case_name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    paths["report_case"] = policy["report_case"]
    opened = False
    close_error = None
    results = {}
    panel_handle = page.locator(selector_for(panel_locator)).first
    panel_visible_before = _element_is_visible(panel_handle)
    scroll_before = _page_scroll_state(page)
    interaction_state = None
    started = time.perf_counter()
    try:
        if panel_visible_before:
            raise AssertionError("interaction panel is visible before open")
        if dismiss_locator:
            dismiss = page.locator(selector_for(dismiss_locator)).first
            try:
                if dismiss.is_visible(timeout=500):
                    _safe_click(dismiss, f"{interaction_name} obstruction")
                    dismiss.wait_for(state="hidden", timeout=5000)
            except Exception as error:
                print(
                    f"WARN [{case_name}] optional obstruction dismiss "
                    f"skipped: {error}"
                )
        trigger = locate_element(page, trigger_locator)
        if not trigger.is_visible():
            raise AssertionError("interaction trigger is not visible")
        _safe_click(trigger, f"{interaction_name} trigger")
        opened = True

        panel = locate_element(page, panel_locator)
        panel.wait_for(state="visible", timeout=10000)
        close = locate_element(page, close_locator)
        close.wait_for(state="visible", timeout=10000)
        bottom_action = (
            locate_element(page, bottom_locator) if bottom_locator else None
        )
        if bottom_action:
            bottom_action.wait_for(state="visible", timeout=10000)

        if not wait_for_layout_stable(panel, timeout=10):
            raise AssertionError("interaction panel layout is not stable")
        state = _panel_state(page, panel, close, bottom_action)
        scroll_open = _page_scroll_state(page)
        interaction_state = {
            **state,
            "panel_visible_before": panel_visible_before,
            "panel_visible_after_open": state.get("panelVisible", False),
            "panel_within_viewport": state.get("withinViewport", False),
            "close_button_visible": state.get("closeVisible", False),
            "bottom_action_visible": state.get("bottomActionVisible", True),
            "horizontal_overflow": bool(
                state.get("pageHorizontalOverflow")
                or state.get("panelHorizontalOverflow")
            ),
            "body_scroll_locked_after_open": scroll_open.get(
                "locked",
                False,
            ),
            "body_scroll_state_before": scroll_before,
            "body_scroll_state_after_open": scroll_open,
        }
        issues = []
        if not state.get("panelVisible"):
            issues.append("panel_not_visible_after_open")
        if not state.get("withinViewport"):
            issues.append("panel_outside_viewport")
        if state.get("pageHorizontalOverflow"):
            issues.append("page_horizontal_overflow")
        if state.get("panelHorizontalOverflow"):
            issues.append("panel_horizontal_overflow")
        if not state.get("closeVisible"):
            issues.append("close_control_not_visible")
        if not state.get("bottomActionVisible"):
            issues.append("bottom_action_obscured")
        if issues:
            raise AssertionError(", ".join(issues))

        capture_target = str(
            config.get("capture_target", "viewport")
        ).strip().lower()
        if capture_target == "panel":
            ctx.artifact_manager.capture_element(
                panel,
                paths["current"],
            )
        elif capture_target == "viewport":
            ctx.artifact_manager.capture_page(
                page,
                paths["current"],
                full_page=False,
            )
        else:
            raise AssertionError(
                "capture_target must be 'panel' or 'viewport'"
            )
        interaction_state["capture_target"] = capture_target
        paths.update(
            {
                "capture_duration_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                "capture_attempts": 1,
                "interaction_state": interaction_state,
                "readonly_interaction": True,
            }
        )
        print(f"OK [{case_name}] read-only panel captured")
        results = {case_name: paths}
    except Exception as error:
        print(f"FAIL [{case_name}] read-only interaction: {error}")
        results = {
            case_name: {
                "error": f"read-only interaction failed: {error}",
                "report_case": policy["report_case"],
                "readonly_interaction": True,
            }
        }
    finally:
        if opened:
            try:
                close = locate_element(page, close_locator)
                _safe_click(close, f"{interaction_name} close")
                panel_handle.wait_for(state="hidden", timeout=10000)
                panel_visible_after_close = _element_is_visible(panel_handle)
                scroll_after = _page_scroll_state(page)
                scroll_probe = _probe_scroll_restored(page)
                body_scroll_restored = bool(
                    not scroll_after.get("locked", False)
                    and scroll_probe.get("moved", False)
                    and scroll_probe.get("restored", False)
                )
                if interaction_state is not None:
                    interaction_state.update(
                        {
                            "panel_visible_after_close": (
                                panel_visible_after_close
                            ),
                            "body_scroll_restored_after_close": (
                                body_scroll_restored
                            ),
                            "body_scroll_state_after_close": scroll_after,
                            "scroll_probe_after_close": scroll_probe,
                        }
                    )
                if panel_visible_after_close:
                    raise AssertionError("panel remains visible after close")
                if not body_scroll_restored:
                    raise AssertionError(
                        "page scrolling was not restored after close"
                    )
                print(f"OK [{case_name}] panel closed")
            except Exception as error:
                close_error = error
        if close_error:
            print(f"FAIL [{case_name}] panel close failed: {close_error}")
            results = {
                case_name: {
                    "error": f"panel close failed: {close_error}",
                    "report_case": policy["report_case"],
                    "readonly_interaction": True,
                }
            }
    return results, []
