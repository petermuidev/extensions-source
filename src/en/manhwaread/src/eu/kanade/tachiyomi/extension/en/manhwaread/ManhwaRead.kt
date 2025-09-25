package eu.kanade.tachiyomi.extension.en.manhwaread

import android.util.Base64
import eu.kanade.tachiyomi.network.GET
import eu.kanade.tachiyomi.network.interceptor.rateLimit
import eu.kanade.tachiyomi.source.model.FilterList
import eu.kanade.tachiyomi.source.model.MangasPage
import eu.kanade.tachiyomi.source.model.Page
import eu.kanade.tachiyomi.source.model.SChapter
import eu.kanade.tachiyomi.source.model.SManga
import eu.kanade.tachiyomi.source.online.HttpSource
import eu.kanade.tachiyomi.util.asJsoup
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import okhttp3.Headers
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Request
import okhttp3.Response
import org.jsoup.nodes.Element
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.text.Regex

class ManhwaRead : HttpSource() {

    override val name = "ManhwaRead"

    override val baseUrl = "https://manhwaread.com"

    override val lang = "en"

    override val supportsLatest = true

    override val client = network.cloudflareClient.newBuilder()
        .rateLimit(2)
        .build()

    override fun headersBuilder(): Headers.Builder = Headers.Builder().apply {
        add("User-Agent", USER_AGENT)
        add("Referer", "$baseUrl/")
        add(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        )
    }

    private val json = Json { ignoreUnknownKeys = true }

    override fun popularMangaRequest(page: Int): Request {
        val url = baseUrl.toHttpUrl().newBuilder()
            .addPathSegment("manhwa")
            .addQueryParameter("sortby", "views")
            .addQueryParameter("order", "desc")
            .addQueryParameter("page", page.toString())
            .build()

        return GET(url, headers)
    }

    override fun popularMangaParse(response: Response): MangasPage = parseMangaPage(response)

    override fun latestUpdatesRequest(page: Int): Request {
        val url = baseUrl.toHttpUrl().newBuilder()
            .addPathSegment("manhwa")
            .addQueryParameter("sortby", "release")
            .addQueryParameter("order", "desc")
            .addQueryParameter("page", page.toString())
            .build()

        return GET(url, headers)
    }

    override fun latestUpdatesParse(response: Response): MangasPage = parseMangaPage(response)

    override fun searchMangaRequest(page: Int, query: String, filters: FilterList): Request {
        if (query.isBlank()) return popularMangaRequest(page)

        val urlBuilder = baseUrl.toHttpUrl().newBuilder()
            .addQueryParameter("s", query)

        if (page > 1) {
            urlBuilder.addQueryParameter("paged", page.toString())
        }

        return GET(urlBuilder.build(), headers)
    }

    override fun searchMangaParse(response: Response): MangasPage = parseMangaPage(response)

    private fun parseMangaPage(response: Response): MangasPage {
        val document = response.asJsoup()

        if (document.selectFirst(".no-results") != null) {
            return MangasPage(emptyList(), false)
        }

        val entries = document.select("div.manga-item.loop-item")
            .mapNotNull { it.toSManga() }

        val hasNextPage = document.selectFirst("a.nextpostslink, .pagination a.next, .wp-pagenavi a.next, .wp-pagenavi a.nextpostslink, link[rel=next]") != null

        return MangasPage(entries, hasNextPage)
    }

    private fun Element.toSManga(): SManga? {
        val link = selectFirst("a.manga-item__link") ?: return null

        return SManga.create().apply {
            title = link.text().trim()
            url = link.absUrl("href").toRelativeUrl()
            thumbnail_url = selectFirst("img.manga-item__img-inner")?.absUrl("src")
        }
    }

    override fun mangaDetailsParse(response: Response): SManga = SManga.create().apply {
        val document = response.asJsoup()
        val summary = document.selectFirst("section#mangaSummary")

        title = summary?.selectFirst("h1")?.text()?.trim().orEmpty()
        val altName = summary?.selectFirst("h2")?.text()?.trim()?.takeUnless(String::isBlank)
        thumbnail_url = summary?.selectFirst("img")?.absUrl("src")

        author = summary?.extractInfo("Author:")
        artist = summary?.extractInfo("Artist:")
        genre = summary?.select(".manga-genres a")
            ?.map { it.text().trim() }
            ?.filter { it.isNotBlank() }
            ?.joinToString()

        status = summary?.selectFirst(".manga-status__label")
            ?.text()
            ?.let(::parseStatus)
            ?: SManga.UNKNOWN

        val synopsis = summary?.selectFirst("#mangaDesc .manga-desc__content")
            ?.text()?.trim()?.takeUnless(String::isBlank)

        description = listOfNotNull(altName, synopsis)
            .joinToString(separator = "\n")
    }

    private fun Element.extractInfo(label: String): String? {
        val labelSelector = "div.text-primary:matchesOwn(^${Regex.escape(label)}$)"
        val labelElement = selectFirst(labelSelector) ?: return null
        return labelElement.parent()?.select("a span:first-child")
            ?.map { it.text().trim() }
            ?.filter { it.isNotBlank() }
            ?.joinToString(", ")
    }

    private fun parseStatus(statusText: String): Int = when (statusText.trim().lowercase(Locale.ENGLISH)) {
        "completed" -> SManga.COMPLETED
        "ongoing" -> SManga.ONGOING
        "hiatus" -> SManga.ON_HIATUS
        "cancelled", "canceled" -> SManga.CANCELLED
        else -> SManga.UNKNOWN
    }

    override fun chapterListParse(response: Response): List<SChapter> {
        val document = response.asJsoup()

        val chapters = document.select("#chaptersList a.chapter-item")
            .map { element ->
                SChapter.create().apply {
                    name = element.selectFirst(".chapter-item__name")?.text()?.trim().orEmpty()
                    url = element.absUrl("href").toRelativeUrl()
                    chapter_number = chapterNumberRegex.find(name)?.groupValues?.getOrNull(1)
                        ?.replace('x', '.')
                        ?.toFloatOrNull()
                        ?: Float.NaN
                    date_upload = element.selectFirst(".chapter-item__date")?.text()
                        ?.let(::parseChapterDate)
                        ?: 0L
                }
            }

        return chapters.reversed()
    }

    private fun parseChapterDate(raw: String): Long = runCatching {
        dateFormat.parse(raw)?.time
    }.getOrNull() ?: 0L

    override fun pageListParse(response: Response): List<Page> {
        val document = response.asJsoup()
        val scriptData = document.selectFirst("script:containsData(var chapterData)")?.data()
            ?: throw Exception("chapterData script not found")

        val jsonPayload = chapterDataRegex.find(scriptData)?.groupValues?.get(1)
            ?: throw Exception("chapterData payload missing")

        val chapterPayload = json.decodeFromString<ChapterPayload>(jsonPayload)
        val decoded = decodeChapterData(chapterPayload.data)
        val pages = json.decodeFromString<List<ChapterPage>>(decoded.toString(Charsets.UTF_8))

        return pages.mapIndexed { index, page ->
            val imageUrl = buildString {
                append(chapterPayload.base.trimEnd('/'))
                append('/')
                append(page.src.trimStart('/'))
            }
            Page(index, "", imageUrl)
        }
    }

    override fun imageRequest(page: Page): Request {
        val imageHeaders = headersBuilder()
            .set("Accept", "image/avif,image/webp,*/*")
            .build()

        return GET(page.imageUrl!!, imageHeaders)
    }

    override fun getFilterList(): FilterList = FilterList()

    override fun imageUrlParse(response: Response): String = throw UnsupportedOperationException()

    companion object {
        private const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

        private val chapterDataRegex = Regex(
            pattern = """var\s+chapterData\s*=\s*(\{.*?})\s*;""",
            options = setOf(RegexOption.DOT_MATCHES_ALL, RegexOption.MULTILINE),
        )

        private val chapterNumberRegex = Regex("chapter\\s+([\\d.]+(?:x\\d+)?)", RegexOption.IGNORE_CASE)

        private val dateFormat = SimpleDateFormat("dd/MM/yyyy", Locale.ENGLISH)

        private val whitespaceRegex = Regex("\\s+")

        private fun decodeChapterData(data: String): ByteArray {
            val sanitized = whitespaceRegex.replace(data, "")
            val padding = (4 - sanitized.length % 4) % 4
            val padded = sanitized + "=".repeat(padding)
            return Base64.decode(padded, Base64.DEFAULT)
        }
    }

    @Serializable
    private data class ChapterPayload(
        val data: String,
        val base: String,
    )

    @Serializable
    private data class ChapterPage(
        val src: String,
    )

    private fun String.toRelativeUrl(): String {
        return if (startsWith("http", ignoreCase = true)) {
            substringAfter(baseUrl, this)
        } else {
            this
        }
    }
}
