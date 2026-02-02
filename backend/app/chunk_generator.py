from typing import List, Iterable


class RecursiveCharacterTextSplitter:
    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        separators: Iterable[str] | None = None,
        keep_separator: bool = False,
    ):
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.keep_separator = keep_separator

        self.separators = list(separators) if separators else [
            "\n\n",
            "\n",
            ". ",
            " ",
            ""
        ]

    def split_text(self, text: str) -> List[str]:
        return self._split_recursive(text, self.separators)

    def _split_recursive(self, text: str, separators: List[str]) -> List[str]:
        if len(text) <= self.chunk_size:
            return [text]

        if not separators:
            return self._merge_splits(list(text))

        sep = separators[0]

        if sep:
            splits = self._split_with_separator(text, sep)
        else:
            splits = list(text)

        final_chunks = []

        for split in splits:
            if len(split) <= self.chunk_size:
                final_chunks.append(split)
            else:
                deeper = self._split_recursive(split, separators[1:])
                final_chunks.extend(deeper)

        return self._merge_splits(final_chunks)

    def _split_with_separator(self, text: str, sep: str) -> List[str]:
        if self.keep_separator:
            parts = text.split(sep)
            splits = []
            for i, part in enumerate(parts):
                if i < len(parts) - 1:
                    splits.append(part + sep)
                else:
                    splits.append(part)
            return splits
        else:
            return text.split(sep)

    def _merge_splits(self, splits: List[str]) -> List[str]:
        chunks = []
        current = ""

        for split in splits:
            if len(current) + len(split) <= self.chunk_size:
                current += split
            else:
                if current:
                    chunks.append(current)
                current = split

        if current:
            chunks.append(current)

        if self.chunk_overlap > 0:
            return self._add_overlap(chunks)

        return chunks

    def _add_overlap(self, chunks: List[str]) -> List[str]:
        overlapped = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapped.append(chunk)
                continue

            overlap = overlapped[-1][-self.chunk_overlap:]
            overlapped.append(overlap + chunk)

        return overlapped
