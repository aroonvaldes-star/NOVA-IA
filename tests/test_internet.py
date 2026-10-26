import pytest

from nova.internet import URLNoPermitida, _ResultadosDDG, extraer_texto, revisar_url


@pytest.mark.parametrize("url", [
    "http://localhost:8190/api", "http://127.0.0.1/", "http://192.168.1.1/admin", "http://10.0.0.5",
    "http://172.16.3.4", "http://169.254.169.254/latest", "http://router.local/", "file:///C:/Windows/win.ini",
    "ftp://x.com", "http://[::1]/", "https://usuario:clave@ejemplo.com", "http://0.0.0.0/",
])
def test_urls_bloqueadas(url):
    with pytest.raises(URLNoPermitida):
        revisar_url(url, resolver=False)


def test_url_publica():
    assert revisar_url("https://93.184.216.34/x", resolver=False)


def test_extraer_texto():
    html = """<html><head><title>Guía</title><script>alert(1)</script></head>
    <body><nav>menú</nav><h1>Vulkan</h1><p>Paso uno &amp; dos.</p><footer>pie</footer></body></html>"""
    titulo, texto = extraer_texto(html)
    assert titulo == "Guía"
    assert "Paso uno & dos." in texto and "alert" not in texto and "menú" not in texto


def test_resultados_duckduckgo():
    html = """<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fggml-org%2Fllama.cpp&rut=x">llama.cpp</a>
    <a class="result__snippet" href="#">Inferencia LLM en C/C++</a></div>
    <div class="result"><a class="result__a" href="https://ejemplo.com/b">Otro</a><a class="result__snippet">Texto B</a></div>"""
    p = _ResultadosDDG()
    p.feed(html)
    p.close()
    assert [r.url for r in p.resultados] == ["https://github.com/ggml-org/llama.cpp", "https://ejemplo.com/b"]
    assert p.resultados[0].fragmento == "Inferencia LLM en C/C++"
