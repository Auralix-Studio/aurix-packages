#!/usr/bin/env python3
"""Servidor del ensayo: sirve la carpeta local y, lo que no esté en ella, lo redirige al repositorio publicado.

Así el ensayo ve el repositorio tal como quedará (lo publicado más lo nuevo) sin bajarlo entero: un paquete que
dependa de otro ya publicado se puede comprobar antes de subir nada.

  python3 ensayo_servidor.py CARPETA URL_PUBLICADA [puerto]
"""
import functools
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, published: str, **kwargs):
        self.published = published.rstrip('/')
        super().__init__(*args, **kwargs)

    def send_head(self):
        if os.path.isfile(self.translate_path(self.path)):
            return super().send_head()
        # Solo los .deb: índices y firmas tienen que ser los del ensayo, nunca los publicados.
        if self.path.startswith('/pool/'):
            self.send_response(302)
            self.send_header('Location', self.published + self.path)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        self.send_error(404)
        return None

    def log_message(self, *args):
        pass


def serve(folder: str, published: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(('127.0.0.1', port), functools.partial(Handler, directory=folder, published=published))


if __name__ == '__main__':
    serve(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 8765).serve_forever()
