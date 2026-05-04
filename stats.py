import calendar
import tempfile
from datetime import date
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

COLORS = [
    "#4C9BE8", "#E87C4C", "#4CE8A0", "#E84C7C",
    "#A04CE8", "#E8C84C", "#4CE8E8", "#E84C4C",
    "#7CE84C", "#4C4CE8", "#E8A04C", "#4CE8C8",
]

_MONTH_UA = ["","Січень","Лютий","Березень","Квітень","Травень","Червень",
             "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]


def _save(fig) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(tmp.name, dpi=140, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return tmp.name


def _intensity_color(intensity: float) -> str:
    if intensity == 0:
        return "#2A2A3E"
    r = int(30 + intensity * 202)
    g = int(30 + intensity * 46)
    b = int(62 + intensity * 14)
    return f"#{r:02X}{g:02X}{b:02X}"


# ── Теплова карта місяця ──────────────────────────────────────────────────────

def month_heatmap(year: int, month: int, daily: list | None = None) -> str | None:
    if daily is None:
        return None
    if not daily:
        return None

    days_in_month = calendar.monthrange(year, month)[1]
    first_weekday = calendar.monthrange(year, month)[0]  # 0=Пн
    day_values    = {int(r["date"].split("-")[2]): r["total"] for r in daily}
    max_val       = max(day_values.values()) if day_values else 1

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#1E1E2E")
    ax.set_facecolor("#1E1E2E")

    for day in range(1, days_in_month + 1):
        col = (first_weekday + day - 1) % 7
        row = (first_weekday + day - 1) // 7
        intensity = day_values.get(day, 0) / max_val
        ax.add_patch(plt.Rectangle(
            (col + 0.05, 5 - row + 0.05), 0.90, 0.90,
            facecolor=_intensity_color(intensity),
            edgecolor="#2E2E3E", linewidth=1
        ))
        ax.text(col + 0.5, 5 - row + 0.70, str(day),
                ha="center", va="center", color="white", fontsize=8)
        if day in day_values:
            ax.text(col + 0.5, 5 - row + 0.28, f"{day_values[day]:.0f}",
                    ha="center", va="center", color="#E0E0E0", fontsize=7)

    for col, dn in enumerate(["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]):
        ax.text(col + 0.5, 6.7, dn, ha="center", va="center",
                color="#9090A0", fontsize=9)

    ax.set_xlim(0, 7)
    ax.set_ylim(0, 7.2)
    ax.axis("off")
    total = sum(day_values.values())
    ax.set_title(
        f"Теплова карта  •  {_MONTH_UA[month]} {year}  •  {total:.2f} ₴",
        color="white", fontsize=11, pad=8
    )
    return _save(fig)


# ── Бар-чарт / пай по категоріях ─────────────────────────────────────────────

def category_bar_chart(year: int, month: int,
                        target_date: str | None = None,
                        cat_totals: list | None = None) -> str | None:
    if not cat_totals:
        return None
    if target_date:
        return _day_pie(target_date, cat_totals=cat_totals)
    return _month_bar(year, month, cat_totals=cat_totals)


def _day_pie(target_date: str, cat_totals: list | None = None) -> str | None:
    if not cat_totals:
        return None

    labels = [r["category_name"] for r in cat_totals]
    values = [r["total"]         for r in cat_totals]
    total  = sum(values)
    colors = COLORS[:len(labels)]

    fig, ax = plt.subplots(figsize=(7, 5))
    fig.patch.set_facecolor("#1E1E2E")
    ax.set_facecolor("#1E1E2E")

    wedges, _, autotexts = ax.pie(
        values,
        autopct=lambda p: f"{p:.1f}%\n({p * total / 100:.0f} ₴)",
        colors=colors,
        startangle=140,
        pctdistance=0.75,
        wedgeprops={"edgecolor": "#2E2E3E", "linewidth": 2},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontsize(9)

    # Форматуємо дату для заголовку
    try:
        from datetime import datetime as _dt
        nice = _dt.strptime(target_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        nice = target_date

    ax.set_title(f"Витрати за {nice}\nРазом: {total:.2f} ₴",
                 color="white", fontsize=13, pad=15)

    patches = [mpatches.Patch(color=colors[i], label=f"{labels[i]} ({values[i]:.2f} ₴)")
               for i in range(len(labels))]
    ax.legend(handles=patches, loc="lower center", bbox_to_anchor=(0.5, -0.15),
              ncol=2, frameon=False, labelcolor="white")

    plt.tight_layout()
    return _save(fig)


def _month_bar(year: int, month: int, cat_totals: list | None = None) -> str | None:
    if not cat_totals:
        return None

    labels = [r["category_name"] for r in reversed(cat_totals)]
    values = [r["total"]         for r in reversed(cat_totals)]
    colors = COLORS[:len(labels)]
    total  = sum(values)

    fig, ax = plt.subplots(figsize=(8, max(3, len(labels) * 0.6 + 1)))
    fig.patch.set_facecolor("#1E1E2E")
    ax.set_facecolor("#1E1E2E")

    bars = ax.barh(labels, values, color=colors, edgecolor="#2E2E3E", height=0.6)

    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + total * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:.2f} ₴  ({val/total*100:.1f}%)",
                va="center", color="white", fontsize=8)

    ax.set_xlim(0, max(values) * 1.35)
    ax.set_xlabel("₴", color="#9090A0")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#3E3E4E")
    ax.set_title(f"По категоріях  •  {_MONTH_UA[month]} {year}  •  {total:.2f} ₴",
                 color="white", fontsize=11, pad=8)

    plt.tight_layout()
    return _save(fig)