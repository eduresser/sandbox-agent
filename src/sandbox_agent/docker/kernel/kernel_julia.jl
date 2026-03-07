using Sockets
using JSON3

const LISTEN_PORT = 8765
const MAX_OUTPUT  = 2 * 1024 * 1024

cd("/workspace")

# ── Persistent User Module ──────────────────────────────

user_module = Module(:Sandbox)

# ── Helpers ─────────────────────────────────────────────

function truncate_str(text::AbstractString, limit::Int=MAX_OUTPUT)
    if sizeof(text) > limit
        half = limit ÷ 2
        last = min(half, lastindex(text))
        return text[1:last] * "\n\n... [TRUNCATED] ...\n"
    end
    text
end

function format_result(val)
    val === nothing && return nothing
    display = Dict{String,Any}()

    try
        html = sprint(show, MIME("text/html"), val)
        if !isempty(html) && sizeof(html) < MAX_OUTPUT
            display["text/html"] = html
        end
    catch
    end

    txt = try
        sprint(
            show, MIME("text/plain"), val;
            context=(:limit => true, :displaysize => (40, 120)),
        )
    catch
        repr(val)
    end
    display["text/plain"] = txt

    display
end

# ── Execution ───────────────────────────────────────────

function execute(code::String, timeout_secs::Int=30)
    timeout_secs = min(timeout_secs, 300)

    orig_stdout = stdout
    orig_stderr = stderr

    out_rd, out_wr = redirect_stdout()
    err_rd, err_wr = redirect_stderr()

    # Drain pipes in background to prevent deadlock on large output.
    out_reader = @async read(out_rd, String)
    err_reader = @async read(err_rd, String)

    timed_out = Ref(false)

    task = @async begin
        try
            val = Base.include_string(user_module, code, "cell")
            (true, val, nothing, nothing)
        catch e
            bt = catch_backtrace()
            (false, nothing, e, bt)
        end
    end

    timer = Timer(timeout_secs)
    @async begin
        wait(timer)
        if !istaskdone(task)
            timed_out[] = true
            try schedule(task, InterruptException(); error=true) catch end
        end
    end

    task_result = try
        fetch(task)
    catch e
        (false, nothing, e, nothing)
    end

    close(timer)

    redirect_stdout(orig_stdout)
    redirect_stderr(orig_stderr)
    close(out_wr)
    close(err_wr)

    out_str = fetch(out_reader)
    err_str = fetch(err_reader)
    close(out_rd)
    close(err_rd)

    ok, val, err, bt = task_result

    response = Dict{String,Any}(
        "success" => true,
        "stdout"  => truncate_str(out_str),
        "stderr"  => truncate_str(err_str),
        "result"  => nothing,
        "error"   => nothing,
        "display_outputs" => Any[],
    )

    if timed_out[]
        response["success"] = false
        response["error"] = Dict{String,Any}(
            "type"    => "TimeoutError",
            "message" => "Execution exceeded $(timeout_secs)s",
        )
    elseif !ok
        response["success"] = false
        actual_err = (err isa Base.LoadError) ? err.error : err
        tb_str = bt !== nothing ? sprint(showerror, err, bt) :
                                  sprint(showerror, err)
        response["error"] = Dict{String,Any}(
            "type"      => string(nameof(typeof(actual_err))),
            "message"   => sprint(showerror, err),
            "traceback" => tb_str,
        )
    else
        response["result"] = format_result(val)
    end

    response
end

# ── Request Handler ─────────────────────────────────────

function handle_request(raw::String)
    local req
    try
        req = JSON3.read(raw, Dict{String,Any})
    catch e
        return Dict{String,Any}(
            "success" => false,
            "error"   => Dict{String,Any}(
                "type"    => "JSONDecodeError",
                "message" => sprint(showerror, e),
            ),
        )
    end

    action = get(req, "action", "execute")

    if action == "execute"
        code = get(req, "code", "")::String
        timeout = get(req, "timeout", 30)
        return execute(code, round(Int, timeout))
    end

    if action == "restart"
        global user_module = Module(:Sandbox)
        return Dict{String,Any}("success" => true, "message" => "Kernel restarted")
    end

    if action == "ping"
        return Dict{String,Any}("success" => true)
    end

    Dict{String,Any}(
        "success" => false,
        "error"   => Dict{String,Any}(
            "type"    => "ValueError",
            "message" => "Unknown action: $action",
        ),
    )
end

# ── TCP Socket Server (same as R; PipeEndpoint/Unix has half-close issues) ─

server = listen(parse(Int, get(ENV, "KERNEL_PORT", "8765")))

println("KERNEL_READY")
flush(stdout)

while true
    local conn
    try
        conn = accept(server)
    catch
        sleep(0.1)
        continue
    end

    try
        # Read until EOF (client_c uses half-close).
        chunks = UInt8[]
        while true
            chunk = read(conn, 65536)
            isempty(chunk) && break
            append!(chunks, chunk)
        end
        data = String(chunks)
        result = handle_request(data)
        write(conn, JSON3.write(result))
        flush(conn)
    catch e
        try
            err_resp = Dict{String,Any}(
                "success" => false,
                "error"   => Dict{String,Any}(
                    "type"    => string(nameof(typeof(e))),
                    "message" => sprint(showerror, e),
                ),
            )
            write(conn, JSON3.write(err_resp))
            flush(conn)
        catch
        end
    finally
        close(conn)
    end
end
