from AppKit import NSWorkspace


def frontmost_app_name():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app.localizedName() if app else None


def style_for_app(app_name, context_cfg):
    if not app_name:
        return context_cfg["default_style"]
    styles = context_cfg["app_styles"]
    if app_name in styles:
        return styles[app_name]
    for key, style in styles.items():
        if key.lower() in app_name.lower():
            return style
    return context_cfg["default_style"]
