package eu.kanade.tachiyomi.extension.th.gomanga

import eu.kanade.tachiyomi.multisrc.mangathemesia.MangaThemesia
import java.text.SimpleDateFormat
import java.util.Locale

class GoManga : MangaThemesia(
    "Go-Manga",
    "https://www.go-manga.com",
    "th",
    dateFormat = SimpleDateFormat("MMMM d, yyyy", Locale("th")),
) {
    // Some MangaThemesia sites label series type with Thai text "ประเภท"
    override val seriesTypeSelector = ".imptdt:contains(ประเภท) a"
}
