import unittest

from rdsbridge import templates as tpl
from rdsbridge import uecp, wideorbit


class WideOrbitParsing(unittest.TestCase):
    def test_simple_document(self):
        ev = wideorbit.parse(b"<nowplaying><title>Song</title><artist>Band</artist>"
                             b"<duration>00:03:05</duration><media_type>SONG</media_type></nowplaying>")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["title"], "Song")
        self.assertEqual(ev[0]["artist"], "Band")
        self.assertEqual(ev[0]["duration_seconds"], "185")

    def test_multiple_events_and_case(self):
        ev = wideorbit.parse(b"<NowPlaying><Event id='1'><Title>A</Title></Event>"
                             b"<Event id='2'><Title>B</Title></Event></NowPlaying>")
        self.assertEqual([e["title"] for e in ev], ["A", "B"])
        self.assertEqual(ev[1]["id"], "2")

    def test_attributes_as_fields(self):
        ev = wideorbit.parse(b'<nowplaying><event title="T" artist="A" type="SONG"/></nowplaying>')
        self.assertEqual((ev[0]["title"], ev[0]["artist"], ev[0]["media_type"]), ("T", "A", "SONG"))

    def test_dirty_xml(self):
        ev = wideorbit.parse(b"\xef\xbb\xbf<nowplaying><title>Rock & Roll</title><artist>Mot\xf6rhead</artist></nowplaying>")
        self.assertEqual(ev[0]["title"], "Rock & Roll")
        self.assertEqual(ev[0]["artist"], "Motörhead")

    def test_bad_xml(self):
        with self.assertRaises(wideorbit.ParseError):
            wideorbit.parse(b"<nowplaying><title>x</nowplaying")

    def test_field_map(self):
        ev = wideorbit.parse(b"<np><songname>X</songname><name>Y</name></np>", field_map={"title": ["songname"]})
        self.assertEqual(ev[0]["title"], "X")

    def test_durations(self):
        p = wideorbit.parse_duration
        self.assertEqual(p("3:45"), 225)
        self.assertEqual(p("225000"), 225)
        self.assertEqual(p("225"), 225)
        self.assertEqual(p("225", "ms"), 0.225)
        self.assertIsNone(p(""))
        self.assertIsNone(p("abc"))

    def test_extract_documents(self):
        stream = (b'<?xml version="1.0"?><nowplaying><title>A</title></nowplaying>\r\n'
                  b"<nowplaying><title>B</title></nowplaying><nowpla")
        docs, rest = wideorbit.extract_documents(stream)
        self.assertEqual(len(docs), 2)
        self.assertTrue(docs[0].startswith(b"<?xml"))
        self.assertEqual(rest, b"<nowpla")
        docs, rest = wideorbit.extract_documents(b"<nowplaying/>")
        self.assertEqual(docs, [b"<nowplaying/>"])


class Templates(unittest.TestCase):
    def test_first_matching_template(self):
        ts = ["{title} by {artist}", "{title}"]
        self.assertEqual(tpl.render_first(ts, {"title": "T", "artist": "A"}), "T by A")
        self.assertEqual(tpl.render_first(ts, {"title": "T", "artist": ""}), "T")
        self.assertEqual(tpl.render_first(ts, {}), "")

    def test_modifiers_and_case_insensitive(self):
        self.assertEqual(tpl.render("{Title!u}|{artist!t}|{title:.3}", {"title": "hello", "artist": "the band"}),
                         "HELLO|The Band|hel")

    def test_no_attribute_access(self):
        self.assertEqual(tpl.render("{title.__class__}", {"title": "x"}), "")

    def test_clean(self):
        self.assertEqual(tpl.clean("Beyoncé – “Halo”\r\n"), 'Beyonce - "Halo"')
        self.assertEqual(tpl.clean("Beyoncé", ascii_only=False), "Beyoncé")

    def test_truncate(self):
        self.assertEqual(tpl.truncate("Here Comes The Sun - The Beatles", 22), "Here Comes The Sun")
        self.assertEqual(tpl.truncate("abcdefghij", 5), "abcde")
        self.assertEqual(tpl.truncate("short", 64), "short")


class Uecp(unittest.TestCase):
    def test_crc_check_value(self):
        # CRC-16/CCITT init FFFF, inverted output (a.k.a. CRC-16/GENIBUS) check value
        self.assertEqual(uecp.crc16(b"123456789"), 0xD64E)

    def test_stuffing(self):
        self.assertEqual(uecp.stuff(b"\x01\xfd\xfe\xff"), b"\x01\xfd\x00\xfd\x01\xfd\x02")

    def test_rt_frame(self):
        f = uecp.frame(uecp.rt_message("Hi", ab_flag=1))
        self.assertEqual(f[0], 0xFE)
        self.assertEqual(f[-1], 0xFF)
        body = f[1:-3]
        self.assertEqual(body[:4], bytes([0, 0, 0, 7]))  # addr, sqc, mfl
        self.assertEqual(body[4:], bytes([0x0A, 0, 0, 3, 0x01]) + b"Hi")

    def test_ps_padded(self):
        self.assertEqual(uecp.ps_message("WAAA"), b"\x02\x00\x00WAAA    ")

    def test_address(self):
        f = uecp.frame(b"\x02", site=1, encoder=2)
        self.assertEqual(f[1:3], bytes([0x00, 0x42]))


if __name__ == "__main__":
    unittest.main()
