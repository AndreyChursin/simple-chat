import data from '@emoji-mart/data';
import Picker from '@emoji-mart/react';

/** Loaded lazily: the emoji data set is large. */
export default function EmojiPicker({ onSelect }: { onSelect: (emoji: string) => void }) {
  return (
    <Picker
      data={data}
      onEmojiSelect={(e: { native: string }) => onSelect(e.native)}
      theme="auto"
      previewPosition="none"
      skinTonePosition="search"
    />
  );
}
