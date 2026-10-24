"""Every form page is on the shell (spec 4.7, 6.2, 9).

Sign in, sign up, password reset, email verification and the device pages sit on layouts/form.html: the shell's skip link and
<main>, one heading, a title of their own, the menu and the mark, and none of the chrome the old pages wrote by hand (a hamburger,
an ambient layer, a theme boot script, a <style>). Every control in <main> carries `interactive`, so it answers the cursor and a
press (spec 4.7). These checks took the place of the old-markup tests that pinned that chrome on the old pages (a hamburger menu,
an ambient layer, a wordmark with an underscore, a skip link written the old way), which Task 3 of this change retired.
"""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

# Form pages a visitor can open with nothing set up. The token addresses are made up: the page for a link that does not work is a page too.
PUBLIC_FORM_PAGES = (
    "/accounts/login/",
    "/accounts/signup/",
    "/accounts/password_reset/",
    "/accounts/password_reset/done/",
    "/accounts/reset/done/",
    "/accounts/reset/MQ/not-a-token/",
    "/accounts/verify-email/not-a-token/",
)
# The ones that need a session: the email page, the password change and the page it ends on, and the device-code entry.
SIGNED_IN_FORM_PAGES = ("/accounts/verify-email/", "/accounts/password_change/", "/accounts/password_change/done/", "/device")
# What the old pages wrote by hand and the shell now gives every page.
OLD_CHROME = ('class="site-menu-wrap"', 'class="ambient"', 'class="ambient-controls"', 'id="veilfile-theme-boot"', "<style")
# A form page's title: its own name, a vertical bar, Veilfile. No em dash (the voice rules), and never the bare word.
TITLE = re.compile(r"[^|—<>]+ \| Veilfile")
CONTROL_TAG = re.compile(r"<(?:button|a)\b[^>]*>")
BODY_CLASS = re.compile(r'<body class="[^"]*\blayout-form\b')


def template_of(response):
    """Where a failure should send the reader: the template the page was drawn from."""
    names = [template.name for template in response.templates if template.name]
    return f"backend/templates/{names[0]}" if names else "the page's template"


def classes_of(tag):
    """The class names in an opening tag."""
    found = re.search(r'\sclass="([^"]*)"', tag)
    return found.group(1).split() if found else []


class FormPagesAreOnTheShellTests(TestCase):
    longMessage = False

    def check(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} answered {response.status_code}, not 200: a form page keeps its status (spec 4.7); "
                         "undo whatever changed it")
        html = response.content.decode()
        page = template_of(response)
        self.assertRegex(html, BODY_CLASS,
                         f"{page}: extend layouts/form.html (backend/templates/layouts/form.html), the narrow column of every form page (spec 6.2); "
                         "its body class is layout-form, and a page may add to it")
        self.assertNotIn("data-layout", html,
                         f"{page}: a form page has no layout family and so no data-layout: the shell would change it in place, and a form must load as an ordinary page")
        skip, main = html.find('<a class="skip-link label" href="#main">'), html.find('<main id="main" class="shell-main">')
        self.assertTrue(0 <= skip < main,
                        f'{page}: the shell\'s skip link must come before <main id="main" class="shell-main">; a form page fills a block of layouts/form.html and writes neither')
        inside = html[main:html.find("</main>", main)]
        self.assertEqual((html.count("<h1"), inside.count("<h1")), (1, 1),
                         f"{page}: one <h1> per page, inside <main> (spec 9). Put the page's heading in {{% block form_page %}} and write no other <h1>")
        title = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        self.assertTrue(title is not None and TITLE.fullmatch(title.group(1).strip()),
                        f"{page}: fill {{% block title %}} with the page's own name, a vertical bar and Veilfile (`Log in | Veilfile`), as every page of the site "
                        "has; no em dash (the voice rules). A form page has no registry entry, so the shell's default is the bare word Veilfile")
        for tag in CONTROL_TAG.findall(inside):
            classes = classes_of(tag)
            if tag.startswith("<button") or "action" in classes:
                self.assertIn("interactive", classes,
                              f"{page}: {tag} is a control without the class `interactive`, so it would not answer the cursor or a press. Write the main action "
                              'as <button class="action interactive" type="submit"> and a quieter one as class="label interactive" (spec 4.7)')
        for chrome in OLD_CHROME:
            self.assertNotIn(chrome, html, f"{page}: has {chrome}, which the old pages wrote by hand. The shell gives the menu, the switches, the head and the "
                             "cloth; delete it from the template")
        self.assertEqual(html.count('<nav class="menu"'), 1, f"{page}: the shell's menu (components/menu.html) must be on the page once")
        self.assertIn('<svg class="mark" viewBox="0 0 560 560" aria-hidden="true"', html,
                      f"{page}: the shell's mark must be on the page and hidden from screen readers (components/mark.html); its name is the lockup's hidden word")
        self.assertNotIn("{#", html, f"{page}: a {{# comment spans lines and Django printed it on the page. Write one {{# #}} per line")
        self.assertNotIn("#}", html, f"{page}: a {{# comment spans lines and Django printed it on the page. Write one {{# #}} per line")

    def test_the_form_pages_a_visitor_can_open(self):
        for url in PUBLIC_FORM_PAGES:
            with self.subTest(url=url):
                self.check(url)

    def test_the_form_pages_that_need_a_session(self):
        self.client.force_login(get_user_model().objects.create_user(username="forms-shell", password="pw12345678"))
        for url in SIGNED_IN_FORM_PAGES:
            with self.subTest(url=url):
                self.check(url)

    def test_the_title_and_control_checks_find_what_they_name(self):
        for title, fits in (("Log in | Veilfile", True), ("Enter device code | Veilfile", True), ("Veilfile", False),
                            ("Log in — Veilfile", False), ("| Veilfile", False), ("Log in | Veilfile | Veilfile", False)):
            self.assertEqual(bool(TITLE.fullmatch(title)), fits,
                             f"backend/pages/tests_forms_on_the_shell.py: TITLE must accept `Name | Veilfile` and refuse the bare word, an em dash and an empty name; {title!r} must be {'accepted' if fits else 'refused'}")
        self.assertEqual(classes_of('<button class="action interactive" type="submit">'), ["action", "interactive"],
                         "backend/pages/tests_forms_on_the_shell.py: classes_of() must list the class names of an opening tag")
        self.assertEqual(classes_of('<a data-class="x" href="/">'), [],
                         "backend/pages/tests_forms_on_the_shell.py: classes_of() must read the class attribute only, not data-class")
        self.assertEqual(CONTROL_TAG.findall('<p>a <a href="/">b</a> <button type="submit">c</button> <abbr>d</abbr> <a class="action">e</a></p>'),
                         ['<a href="/">', '<button type="submit">', '<a class="action">'],
                         "backend/pages/tests_forms_on_the_shell.py: CONTROL_TAG must find each <a> and <button> opening tag, and no other tag that begins with a or b")
