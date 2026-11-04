"""4/11 sera: elenco di chi ha aperto la campagna; nei log solo impronte corte, mai email."""
import gzip
import io
import sys
import types
import zipfile

sys.modules.setdefault("httpx", types.SimpleNamespace(Client=object, get=None))


def test_email_da_export_zip_gzip_csv():
    from dealfinder.marketing.brevo_liste import emails_da_export, hash_email
    csv = b"EMAIL;NOME\nA@x.it;a\nb@y.com;b\na@x.it;a\n"
    assert emails_da_export(csv) == ["a@x.it", "b@y.com"]
    assert emails_da_export(gzip.compress(csv)) == ["a@x.it", "b@y.com"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("f.csv", csv)
    assert emails_da_export(buf.getvalue()) == ["a@x.it", "b@y.com"]
    assert hash_email(" A@x.it ") == hash_email("a@x.it") and len(hash_email("a@x.it")) == 12
