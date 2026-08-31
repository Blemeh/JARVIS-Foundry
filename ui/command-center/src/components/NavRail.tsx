import { useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Cpu } from "lucide-react";

type NavSection = {
  id: string;
  label: string;
  to: string;
  end?: boolean;
  children?: { label: string; to: string }[];
};

const sections: NavSection[] = [
  { id: "jarvis", label: "JARVIS", to: "/", end: true },
  {
    id: "operations",
    label: "OPERATIONS",
    to: "/operations",
    children: [
      { label: "Operations hub", to: "/operations" },
      { label: "Missions", to: "/missions" },
      { label: "Agents", to: "/agents" },
      { label: "Executive", to: "/executive" },
    ],
  },
  {
    id: "intelligence",
    label: "INTELLIGENCE",
    to: "/discovery",
    children: [
      { label: "Discovery", to: "/discovery" },
      { label: "Memory", to: "/memory" },
      { label: "Evidence", to: "/evidence" },
    ],
  },
  {
    id: "systems",
    label: "SYSTEMS",
    to: "/systems",
    children: [
      { label: "Runtime", to: "/systems" },
      { label: "Timeline", to: "/timeline" },
      { label: "Notifications", to: "/notifications" },
      { label: "Owner", to: "/owner" },
    ],
  },
];

function isSectionActive(section: NavSection, pathname: string): boolean {
  if (section.end && pathname === "/") return true;
  if (section.id === "jarvis") return pathname === "/";
  const prefixes = [section.to, ...(section.children?.map((c) => c.to) ?? [])];
  return prefixes.some((p) => p !== "/" && (pathname === p || pathname.startsWith(`${p}/`)));
}

export function NavRailContent({ variant = "rail" }: { variant?: "rail" | "drawer" }) {
  const location = useLocation();
  const [openId, setOpenId] = useState<string | null>(null);

  if (variant === "drawer") {
    const flatLinks = sections.flatMap((section) =>
      section.children?.length
        ? section.children.map((child) => ({ ...child, section: section.label }))
        : [{ label: section.label, to: section.to, section: section.label }],
    );
    return (
      <nav className="nav-rail nav-rail--drawer" aria-label="Primary">
        <ul className="nav-rail-drawer-list">
          {flatLinks.map((link) => (
            <li key={link.to}>
              <NavLink
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) => `nav-rail-drawer-link ${isActive ? "nav-rail-drawer-link--active" : ""}`}
                onClick={() => setOpenId(null)}
              >
                {link.section !== link.label ? (
                  <span className="nav-rail-drawer-section" aria-hidden="true">{link.section}</span>
                ) : null}
                <span>{link.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    );
  }

  return (
      <nav className="nav-rail" aria-label="Primary">
      <div className="nav-rail-brand" aria-hidden="true">
        <span className="nav-rail-logo">
          <Cpu size={16} />
        </span>
      </div>
      <ul className="nav-rail-list">
        {sections.map((section) => {
          const active = isSectionActive(section, location.pathname);
          const hasFlyout = (section.children?.length ?? 0) > 0;
          return (
            <li
              key={section.id}
              className="nav-rail-item"
              onMouseEnter={() => hasFlyout && setOpenId(section.id)}
              onMouseLeave={() => setOpenId((id) => (id === section.id ? null : id))}
              onFocus={() => hasFlyout && setOpenId(section.id)}
              onBlur={(e) => {
                if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                  setOpenId((id) => (id === section.id ? null : id));
                }
              }}
            >
              <NavLink
                to={section.to}
                end={section.end}
                className={`nav-rail-link ${active ? "nav-rail-link--active" : ""}`}
              >
                <span className="nav-rail-link-text">{section.label}</span>
              </NavLink>
              {hasFlyout && openId === section.id ? (
                <div className="nav-rail-flyout" role="menu">
                  <span className="nav-rail-flyout-title">{section.label}</span>
                  <ul>
                    {section.children!.map((child) => (
                      <li key={child.to}>
                        <NavLink
                          to={child.to}
                          className={({ isActive }) => `nav-rail-flyout-link ${isActive ? "nav-rail-flyout-link--active" : ""}`}
                          role="menuitem"
                          onClick={() => setOpenId(null)}
                        >
                          {child.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      <ul className="nav-rail-a11y-list" aria-label="All primary destinations">
        {sections.flatMap((section) =>
          section.children?.length
            ? section.children
            : [{ label: section.id === "jarvis" ? "Overview" : section.label, to: section.to }],
        ).map((link) => (
          <li key={`a11y-${link.to}`}>
            <NavLink to={link.to} end={link.to === "/"}>
              {link.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function NavRail() {
  return (
    <aside className="nav-rail-shell" aria-label="Command navigation">
      <NavRailContent />
    </aside>
  );
}
