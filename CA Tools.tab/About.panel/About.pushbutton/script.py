# -*- coding: utf-8 -*-
"""Show developer contact info: LinkedIn, GitHub, Website."""
__title__ = "About"
__author__ = "Chulan"
__doc__ = "Click to see ways to connect with the developer."

import os
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
clr.AddReference("System")

import System.Windows.Forms as WinForms
import System.Drawing as Drawing
from System.Diagnostics import Process, ProcessStartInfo

SCRIPT_DIR = os.path.dirname(__file__)
ICONS_DIR = os.path.join(SCRIPT_DIR, "icons")

# ----------------------------------------------------------------------
# Contact info - edit these if they ever change
# ----------------------------------------------------------------------
LINKEDIN_URL = "https://www.linkedin.com/in/iamchulan/"
GITHUB_URL = "https://github.com/CATools2026"
WEBSITE_URL = "https://catools2026.github.io/CA-Tools-Portfolio/"
YOUTUBE_URL = "https://youtube.com/@catools_2k26?si=GpKz5AXFpfdJXr97"

# ----------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------
DARK_BLUE = Drawing.Color.FromArgb(21, 42, 79)          # header banner
DARK_BLUE_SUB = Drawing.Color.FromArgb(150, 175, 215)   # subtitle on banner
LIGHT_PURPLE = Drawing.Color.FromArgb(127, 119, 221)    # button base (#7F77DD)
LIGHT_PURPLE_HOVER = Drawing.Color.FromArgb(146, 139, 228)


def open_url(url):
    """Open a URL / mailto link with the system default handler."""
    try:
        psi = ProcessStartInfo(url)
        psi.UseShellExecute = True
        Process.Start(psi)
    except Exception as ex:
        WinForms.MessageBox.Show(
            "Could not open link:\n{}\n\n{}".format(url, str(ex)),
            "Error",
            WinForms.MessageBoxButtons.OK,
            WinForms.MessageBoxIcon.Error
        )


class AboutForm(WinForms.Form):
    def __init__(self):
        self.Text = "About the Developer"
        self.Width = 340
        self.Height = 375
        self.StartPosition = WinForms.FormStartPosition.CenterScreen
        self.FormBorderStyle = WinForms.FormBorderStyle.FixedDialog
        self.MaximizeBox = False
        self.MinimizeBox = False
        self.BackColor = Drawing.Color.White
        self.Font = Drawing.Font("Segoe UI", 9)

        # ---- Header banner (A) - dark blue ----
        header = WinForms.Panel()
        header.BackColor = DARK_BLUE
        header.Width = 340
        header.Height = 90
        header.Location = Drawing.Point(0, 0)
        self.Controls.Add(header)

        title = WinForms.Label()
        title.Text = "Connect with Me"
        title.Font = Drawing.Font("Segoe UI", 14, Drawing.FontStyle.Bold)
        title.ForeColor = Drawing.Color.White
        title.BackColor = DARK_BLUE
        title.AutoSize = False
        title.TextAlign = Drawing.ContentAlignment.MiddleCenter
        title.Width = 320
        title.Height = 30
        title.Location = Drawing.Point(10, 22)
        header.Controls.Add(title)

        subtitle = WinForms.Label()
        subtitle.Text = "Choose how you'd like to reach out"
        subtitle.Font = Drawing.Font("Segoe UI", 9)
        subtitle.ForeColor = DARK_BLUE_SUB
        subtitle.BackColor = DARK_BLUE
        subtitle.AutoSize = False
        subtitle.TextAlign = Drawing.ContentAlignment.MiddleCenter
        subtitle.Width = 320
        subtitle.Height = 20
        subtitle.Location = Drawing.Point(10, 55)
        header.Controls.Add(subtitle)

        # ---- Buttons (B) - light purple ----
        btn_linkedin = self._make_button("LinkedIn Profile", "linkedin.png", 115)
        btn_linkedin.Click += self.on_linkedin
        self.Controls.Add(btn_linkedin)

        btn_github = self._make_button("GitHub", "github.png", 165)
        btn_github.Click += self.on_github
        self.Controls.Add(btn_github)

        btn_website = self._make_button("Website", "website.png", 215)
        btn_website.Click += self.on_website
        self.Controls.Add(btn_website)

        btn_youtube = self._make_button("YouTube", "youtube.png", 265)
        btn_youtube.Click += self.on_youtube
        self.Controls.Add(btn_youtube)

    def _make_button(self, text, icon_file, y):
        btn = WinForms.Button()
        btn.Text = "   " + text
        btn.Width = 260
        btn.Height = 42
        btn.Location = Drawing.Point(40, y)
        btn.BackColor = LIGHT_PURPLE
        btn.ForeColor = Drawing.Color.White
        btn.FlatStyle = WinForms.FlatStyle.Flat
        btn.FlatAppearance.BorderSize = 0
        btn.FlatAppearance.MouseOverBackColor = LIGHT_PURPLE_HOVER
        btn.FlatAppearance.MouseDownBackColor = LIGHT_PURPLE_HOVER
        btn.Font = Drawing.Font("Segoe UI", 10, Drawing.FontStyle.Bold)
        btn.Cursor = WinForms.Cursors.Hand
        btn.TextAlign = Drawing.ContentAlignment.MiddleCenter

        icon_path = os.path.join(ICONS_DIR, icon_file)
        if os.path.exists(icon_path):
            img = Drawing.Image.FromFile(icon_path)
            btn.Image = img
            btn.ImageAlign = Drawing.ContentAlignment.MiddleLeft
            btn.TextImageRelation = WinForms.TextImageRelation.ImageBeforeText
            btn.Padding = WinForms.Padding(12, 0, 0, 0)

        return btn

    def on_linkedin(self, sender, args):
        open_url(LINKEDIN_URL)

    def on_github(self, sender, args):
        open_url(GITHUB_URL)

    def on_website(self, sender, args):
        if WEBSITE_URL:
            open_url(WEBSITE_URL)
        else:
            WinForms.MessageBox.Show(
                "Website coming soon!",
                "Not Available Yet",
                WinForms.MessageBoxButtons.OK,
                WinForms.MessageBoxIcon.Information
            )

    def on_youtube(self, sender, args):
        open_url(YOUTUBE_URL)


if __name__ == "__main__":
    form = AboutForm()
    form.ShowDialog()
