from __future__ import annotations

from typing import Optional

import discord

class PaginatorView(discord.ui.View):

    def __init__(self, pages: list[discord.Embed], timeout: float = 120):
        super().__init__(timeout=timeout)
        if not pages:
            raise ValueError("At least one page is required.")
        self.pages = pages
        self.current = 0
        self.message: Optional[discord.Message] = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current == 0
        self.next_btn.disabled = self.current == len(self.pages) - 1

    def _current_embed(self) -> discord.Embed:
        embed = self.pages[self.current]
        embed.set_footer(text=f"Page {self.current + 1} / {len(self.pages)}")
        return embed

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = max(0, self.current - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current = min(len(self.pages) - 1, self.current + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True  # type: ignore[union-attr]
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]
