#
# Persistent R kernel.
# Runs as PID 1 inside the container. Keeps a user environment alive
# and accepts commands via TCP socket on localhost.
#
# No HTTP. No Shiny. No extra dependencies beyond jsonlite and base64enc.
#

library(jsonlite)
library(base64enc)

LISTEN_PORT <- 8765L
MAX_OUTPUT  <- 2L * 1024L * 1024L

setwd("/workspace")

user_lib <- Sys.getenv("R_LIBS_USER", "/usr/local/lib/R/user-library")
dir.create(user_lib, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(user_lib, .libPaths()))

# PPM repo and HTTPUserAgent are set globally via /etc/R/Rprofile.site
# so all R processes (kernel, install_cmd, user terminal) use PPM binaries.

# User code runs in a dedicated child environment so that kernel-internal
# objects (functions, constants) in globalenv() are never clobbered.
.user_env <- new.env(parent = globalenv())

# ── Helpers ──────────────────────────────────────────────

truncate_str <- function(text, limit = MAX_OUTPUT) {
  if (nchar(text) > limit) {
    paste0(substr(text, 1L, limit %/% 2L), "\n\n... [TRUNCATED] ...\n")
  } else {
    text
  }
}

is_ggplot <- function(obj) {
  inherits(obj, "ggplot") || inherits(obj, "gg")
}

capture_ggplot <- function(result_val) {
  outputs <- list()
  plot_file <- tempfile(fileext = ".png")
  on.exit(unlink(plot_file), add = TRUE)

  if (!is.null(result_val) && is_ggplot(result_val)) {
    grDevices::png(plot_file, width = 800, height = 600, res = 100)
    tryCatch({ print(result_val); grDevices::dev.off() },
             error = function(e) tryCatch(grDevices::dev.off(), error = function(x) {}))
    if (file.exists(plot_file) && file.info(plot_file)$size > 200L) {
      outputs[[length(outputs) + 1L]] <- list(type = "image/png", data = base64encode(plot_file))
    }
  }
  outputs
}

capture_htmlwidget <- function(val) {
  if (!requireNamespace("htmlwidgets", quietly = TRUE)) return(NULL)
  if (!inherits(val, "htmlwidget")) return(NULL)
  tmp <- tempfile(fileext = ".html")
  on.exit(unlink(tmp), add = TRUE)
  tryCatch({
    htmlwidgets::saveWidget(val, tmp, selfcontained = TRUE)
    html <- paste(readLines(tmp, warn = FALSE), collapse = "\n")
    if (nchar(html) > 0L) list(type = "text/html", data = html) else NULL
  }, error = function(e) NULL)
}

# ── Execution ────────────────────────────────────────────
# Uses sink() for output redirection so that setTimeLimit() can properly
# interrupt long-running R-level code.

execute <- function(code, timeout = 30) {
  timeout <- min(timeout, 300)

  response <- list(
    success = TRUE,
    stdout  = "",
    stderr  = "",
    result  = NULL,
    error   = NULL,
    display_outputs = list()
  )

  stdout_file <- tempfile()
  stderr_file <- tempfile()

  # Open a PNG device before execution so base-R plots land in it.
  plot_file <- tempfile(fileext = ".png")
  grDevices::png(plot_file, width = 800, height = 600, res = 100)
  plot_dev <- grDevices::dev.cur()

  stdout_con <- file(stdout_file, open = "wt")
  stderr_con <- file(stderr_file, open = "wt")

  sink(stdout_con, type = "output")
  sink(stderr_con, type = "message")

  tryCatch({
    setTimeLimit(elapsed = timeout, transient = TRUE)
    result_val <- withVisible(eval(parse(text = code), envir = .user_env))
    setTimeLimit(elapsed = Inf)

    sink(type = "message")
    sink(type = "output")
    close(stderr_con)
    close(stdout_con)

    tryCatch(grDevices::dev.off(plot_dev), error = function(e) {})
    plot_dev <- NULL

    response$stdout <- truncate_str(paste(readLines(stdout_file, warn = FALSE), collapse = "\n"))
    response$stderr <- truncate_str(paste(readLines(stderr_file, warn = FALSE), collapse = "\n"))

    if (result_val$visible && !is.null(result_val$value)) {
      repr <- paste(utils::capture.output(print(result_val$value)), collapse = "\n")
      response$result <- list("text/plain" = repr)
    }

    display_outputs <- list()
    if (file.exists(plot_file) && file.info(plot_file)$size > 200L) {
      display_outputs[[length(display_outputs) + 1L]] <- list(
        type = "image/png", data = base64encode(plot_file)
      )
    }

    ggplot_outs <- capture_ggplot(result_val$value)
    if (length(ggplot_outs) > 0L) {
      display_outputs <- c(display_outputs, ggplot_outs)
    }

    widget_out <- capture_htmlwidget(result_val$value)
    if (!is.null(widget_out)) {
      display_outputs[[length(display_outputs) + 1L]] <- widget_out
    }

    response$display_outputs <- display_outputs

  }, error = function(e) {
    setTimeLimit(elapsed = Inf)

    tryCatch(sink(type = "message"), error = function(x) {})
    tryCatch(sink(type = "output"), error = function(x) {})
    tryCatch(close(stderr_con), error = function(x) {})
    tryCatch(close(stdout_con), error = function(x) {})

    if (!is.null(plot_dev)) {
      tryCatch(grDevices::dev.off(plot_dev), error = function(x) {})
    }

    response$success <<- FALSE
    response$stdout <<- truncate_str(paste(readLines(stdout_file, warn = FALSE), collapse = "\n"))
    response$stderr <<- truncate_str(paste(readLines(stderr_file, warn = FALSE), collapse = "\n"))
    response$error <<- list(
      type      = class(e)[1L],
      message   = conditionMessage(e),
      traceback = paste(utils::capture.output(traceback()), collapse = "\n")
    )
  })

  unlink(c(stdout_file, stderr_file, plot_file))
  response
}

# ── Request Handler ──────────────────────────────────────

handle_request <- function(raw_json) {
  req <- tryCatch(
    fromJSON(raw_json, simplifyVector = FALSE),
    error = function(e) NULL
  )

  if (is.null(req)) {
    return(list(success = FALSE,
                error   = list(type = "JSONDecodeError",
                               message = "Invalid JSON")))
  }

  action <- req$action %||% "execute"

  if (action == "execute") {
    return(execute(req$code %||% "", req$timeout %||% 30))
  }

  if (action == "restart") {
    rm(list = ls(envir = .user_env), envir = .user_env)
    return(list(success = TRUE, message = "Kernel restarted"))
  }

  if (action == "ping") {
    return(list(success = TRUE))
  }

  list(success = FALSE,
       error   = list(type = "ValueError",
                      message = paste0("Unknown action: ", action)))
}

# ── TCP Socket Server ───────────────────────────────────

cat("KERNEL_READY\n")
flush.console()

repeat {
  con <- tryCatch(
    socketConnection(
      host    = "127.0.0.1",
      port    = LISTEN_PORT,
      server  = TRUE,
      blocking = TRUE,
      open    = "r+b"
    ),
    error = function(e) {
      Sys.sleep(0.1)
      NULL
    }
  )

  if (is.null(con)) next

  tryCatch({
    raw_bytes <- raw(0)
    repeat {
      chunk <- readBin(con, what = "raw", n = 65536L)
      if (length(chunk) == 0L) break
      raw_bytes <- c(raw_bytes, chunk)
    }

    raw_json <- rawToChar(raw_bytes)
    result   <- handle_request(raw_json)

    json_out <- toJSON(result, auto_unbox = TRUE, null = "null",
                       force = TRUE, pretty = FALSE)
    writeBin(charToRaw(json_out), con)
  }, error = function(e) {
    tryCatch({
      err_json <- toJSON(
        list(success = FALSE,
             error   = list(type = class(e)[1L],
                            message = conditionMessage(e))),
        auto_unbox = TRUE
      )
      writeBin(charToRaw(err_json), con)
    }, error = function(x) {})
  })

  tryCatch(close(con), error = function(e) {})
}
