import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  formatObsidianNoteLabel,
  orbitPosition,
  type ObsidianVaultFile,
} from "../lib/obsidianVault";

type ObsidianOrbitCoreProps = {
  files: ObsidianVaultFile[];
};

export function ObsidianOrbitCore({ files }: ObsidianOrbitCoreProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const activeFiles = useMemo(
    () => (files.length > 0 ? files : []),
    [files],
  );

  const selected = useMemo(() => {
    if (selectedPath && activeFiles.some((f) => f.relPath === selectedPath)) {
      return activeFiles.find((f) => f.relPath === selectedPath) ?? null;
    }
    return activeFiles[0] ?? null;
  }, [activeFiles, selectedPath]);

  if (activeFiles.length === 0) {
    return (
      <div className="obsidian-orbit-core obsidian-orbit-core--empty" role="img" aria-label="Obsidian vault not indexed">
        <div className="obsidian-orbit-rings" aria-hidden="true">
          <span className="obsidian-orbit-ring obsidian-orbit-ring--one" />
          <span className="obsidian-orbit-ring obsidian-orbit-ring--two" />
          <span className="obsidian-orbit-ring obsidian-orbit-ring--three" />
        </div>
        <div className="obsidian-orbit-center">
          <span className="obsidian-orbit-glyph" aria-hidden="true">◆</span>
          <span className="obsidian-orbit-hub-label">OBSIDIAN</span>
          <span className="obsidian-orbit-hub-note">Vault not indexed</span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="obsidian-orbit-core"
      role="group"
      aria-label={`Obsidian vault orbit · ${activeFiles.length} indexed notes`}
    >
      <div className="obsidian-orbit-rings" aria-hidden="true">
        <span className="obsidian-orbit-ring obsidian-orbit-ring--one" />
        <span className="obsidian-orbit-ring obsidian-orbit-ring--two" />
        <span className="obsidian-orbit-ring obsidian-orbit-ring--three" />
      </div>

      <div className="obsidian-orbit-notes">
        {activeFiles.map((file, index) => {
          const pos = orbitPosition(index, activeFiles.length);
          const label = formatObsidianNoteLabel(file.relPath);
          const isActive = selected?.relPath === file.relPath;
          return (
            <button
              key={file.relPath}
              type="button"
              className={`obsidian-orbit-note${isActive ? " obsidian-orbit-note--active" : ""}`}
              style={{ left: `${pos.left}%`, top: `${pos.top}%` }}
              onClick={() => setSelectedPath(file.relPath)}
              aria-pressed={isActive}
              aria-label={`Obsidian note ${label}`}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className="obsidian-orbit-center">
        <span className="obsidian-orbit-glyph" aria-hidden="true">◆</span>
        <span className="obsidian-orbit-hub-label">OBSIDIAN</span>
        {selected ? (
          <Link to="/memory" className="obsidian-orbit-hub-note" title={selected.relPath}>
            {formatObsidianNoteLabel(selected.relPath)}
          </Link>
        ) : null}
      </div>
    </div>
  );
}
