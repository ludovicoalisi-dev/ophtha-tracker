library(jsonlite)
library(rvest)
library(httr2)
library(stringr)
library(lubridate)
library(dplyr)

TARGETS_PATH     <- "data/targets.json"
CONFERENCES_PATH <- "data/conferences.json"

targets <- fromJSON(TARGETS_PATH)
targets <- targets[targets$active == TRUE, ]

existing_data <- if (file.exists(CONFERENCES_PATH) && file.size(CONFERENCES_PATH) > 5) {
  fromJSON(CONFERENCES_PATH)
} else {
  data.frame()
}

fetch_html <- function(url) {
  tryCatch({
    req <- request(url) %>%
      req_headers("User-Agent" = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36") %>%
      req_timeout(15) %>%
      req_perform()
    resp_body_html(req)
  }, error = function(e) {
    message(sprintf("[WARN] Errore connessione a %s: %s", url, e$message))
    NULL
  })
}

extract_dates_heuristic <- function(page_text) {
  clean_text <- str_replace_all(page_text, "\\s+", " ")
  date_pattern <- "\\b(\\d{1,2}\\s+(?:January|February|March|April|May|June|July|August|September|October|November|December|Gennaio|Febbraio|Marzo|Aprile|Maggio|Giugno|Luglio|Agosto|Settembre|Ottobre|Novembre|Dicembre)\\s+\\d{4})\\b"
  
  abstract_context <- str_extract(clean_text, regex(paste0("(?i)(abstract|submission|call for papers).{0,60}?", date_pattern), dotall = TRUE))
  abstract_date_raw <- str_extract(abstract_context, date_pattern)
  
  abstract_date <- tryCatch({
    as.character(parse_date_time(abstract_date_raw, orders = c("d B Y", "B d Y", "d m Y")))
  }, error = function(e) NA_character_)
  
  list(
    abstract_deadline = ifelse(is.na(abstract_date), NA_character_, as.character(as.Date(abstract_date)))
  )
}

updated_records <- list()

for (i in seq_len(nrow(targets))) {
  tgt <- targets[i, ]
  message(sprintf("Processing [%s]: %s...", tgt$id, tgt$name))
  
  prev_record <- if (nrow(existing_data) > 0 && tgt$id %in% existing_data$id) {
    existing_data[existing_data$id == tgt$id, ][1, ]
  } else {
    NULL
  }
  
  html <- fetch_html(tgt$base_url)
  
  extracted_abstract <- NA_character_
  if (!is.null(html)) {
    page_text <- html_text2(html)
    parsed <- extract_dates_heuristic(page_text)
    extracted_abstract <- parsed$abstract_deadline
  }
  
  final_abstract <- if (!is.na(extracted_abstract)) {
    extracted_abstract
  } else if (!is.null(prev_record) && !is.na(prev_record$abstract_deadline)) {
    prev_record$abstract_deadline
  } else {
    NA_character_
  }
  
  record <- list(
    id                = tgt$id,
    name              = tgt$name,
    scope             = tgt$scope,
    subspecialty      = tgt$subspecialty,
    url               = tgt$base_url,
    abstract_deadline = final_abstract,
    last_checked      = as.character(Sys.Date())
  )
  
  updated_records[[i]] <- record
}

output_df <- bind_rows(lapply(updated_records, as.data.frame))
write(toJSON(output_df, pretty = TRUE), file = CONFERENCES_PATH)
message(sprintf("Aggiornamento completato: %d record scritti.", nrow(output_df)))

