import time


def _stable_display_entries(page_config=None):
    value = (page_config or {}).get("stable_display", [])

    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []

    entries = []
    for entry in value:
        if not isinstance(entry, dict) or not entry.get("selector"):
            continue

        try:
            index = max(0, int(entry.get("index", 0)))
        except (TypeError, ValueError):
            index = 0

        entries.append({
            "selector": str(entry["selector"]),
            "index": index,
            "display": str(entry.get("display", "block")),
            "wrapper_selector": str(entry.get("wrapper_selector", "")),
        })

    return entries


def stabilize_configured_display(page, page_config=None):
    """Show one configured item from each rotating marketing component."""

    entries = _stable_display_entries(page_config)
    if not entries:
        return

    page.evaluate(
        """
        (entries) => {
            entries.forEach((entry) => {
                const items = Array.from(document.querySelectorAll(entry.selector));
                if (!items.length) return;

                const selectedIndex = Math.min(entry.index, items.length - 1);
                items.forEach((item, index) => {
                    const selected = index === selectedIndex;
                    item.style.setProperty('animation', 'none', 'important');
                    item.style.setProperty('transition', 'none', 'important');
                    item.style.setProperty('transform', 'none', 'important');
                    item.style.setProperty('display', selected ? entry.display : 'none', 'important');
                    item.style.setProperty('visibility', selected ? 'visible' : 'hidden', 'important');
                    item.style.setProperty('opacity', selected ? '1' : '0', 'important');
                    item.setAttribute('aria-hidden', selected ? 'false' : 'true');
                    item.classList.toggle('is-selected', selected);
                });

                if (entry.wrapper_selector) {
                    document.querySelectorAll(entry.wrapper_selector).forEach((wrapper) => {
                        wrapper.style.setProperty('animation', 'none', 'important');
                        wrapper.style.setProperty('transition', 'none', 'important');
                        wrapper.style.setProperty('transform', 'none', 'important');
                    });
                }
            });
        }
        """,
        entries,
    )
    time.sleep(0.2)


def _stable_card_media_config(page_config=None):
    value = (page_config or {}).get("stable_card_media")
    if not isinstance(value, dict) or not value.get("selector"):
        return None

    def index_for(name, default):
        try:
            return max(0, int(value.get(name, default)))
        except (TypeError, ValueError):
            return default

    return {
        "selector": str(value["selector"]),
        "default_index": index_for("default_index", 0),
        "hover_index": index_for("hover_index", 1),
    }


def stabilize_card_media(card, page_config=None, hover=False):
    """Keep product-card media deterministic before a normal or hover capture."""

    config = _stable_card_media_config(page_config)
    if not config:
        return

    card.evaluate(
        """
        (card, config) => {
            const images = Array.from(card.querySelectorAll(config.selector));
            if (!images.length) return;

            const requestedIndex = config.hover_index;
            const selectedIndex = Math.min(requestedIndex, images.length - 1);
            images.forEach((image, index) => {
                const selected = index === selectedIndex;
                image.style.setProperty('animation', 'none', 'important');
                image.style.setProperty('transition', 'none', 'important');
                image.style.setProperty('display', selected ? 'block' : 'none', 'important');
                image.style.setProperty('visibility', selected ? 'visible' : 'hidden', 'important');
                image.style.setProperty('opacity', selected ? '1' : '0', 'important');
                image.style.setProperty('pointer-events', 'none', 'important');
            });
        }
        """,
        {
            "selector": config["selector"],
            "hover_index": config["hover_index"] if hover else config["default_index"],
        },
    )
