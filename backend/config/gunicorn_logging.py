"""gunicorn logger class that keeps secrets out of the access log.

entrypoint.sh passes ``--logger-class config.gunicorn_logging.RedactingLogger``.
The access log keeps gunicorn's default format (method, path, status, size,
referrer, user agent), but every atom that can carry a bearer secret is
redacted first (see config.log_redaction): the request line and path, the
query string, the Referer (reduced to its origin), credential headers, and
the raw WSGI environ atoms.

Imported by the gunicorn master before the Django app is loaded, so this
module must not import Django.
"""

from gunicorn.glogging import Logger

from config.log_redaction import REDACTED, redact_path, redact_referer, redact_uri

_SECRET_HEADER_ATOMS = (
    "{authorization}i",
    "{proxy-authorization}i",
    "{cookie}i",
    "{x-api-key}i",
    "{set-cookie}o",
)


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        atoms = super().atoms(resp, req, environ, request_time)
        atoms["r"] = "%s %s %s" % (
            environ.get("REQUEST_METHOD", "-"),
            redact_uri(environ.get("RAW_URI", "")),
            environ.get("SERVER_PROTOCOL", "-"),
        )
        atoms["U"] = redact_path(environ.get("PATH_INFO", ""))
        atoms["q"] = REDACTED if environ.get("QUERY_STRING") else ""
        atoms["f"] = redact_referer(environ.get("HTTP_REFERER", "-"))
        for name in list(atoms):
            if name.endswith("}e"):
                # Raw WSGI environ: RAW_URI, QUERY_STRING, HTTP_COOKIE, ...
                del atoms[name]
            elif name in _SECRET_HEADER_ATOMS:
                atoms[name] = REDACTED
            elif name == "{referer}i":
                atoms[name] = redact_referer(atoms[name])
            elif name == "{location}o":
                atoms[name] = redact_uri(atoms[name])
        return atoms
