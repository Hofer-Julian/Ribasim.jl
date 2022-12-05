module ribasim_cli

using Ribasim

function help(x)::Cint
    println(x)
    println("Usage: ribasim path/to/config.toml")
    return 1
end

function julia_main()::Cint
    n = length(ARGS)
    if n != 1
        return help("Only 1 argument expected, got $n")
    end
    toml_path = only(ARGS)
    if !isfile(toml_path)
        return help("File not found: $toml_path")
    end

    try
        Ribasim.run(toml_path)
    catch
        Base.invokelatest(Base.display_error, Base.catch_stack())
        return 1
    end

    return 0
end

if abspath(PROGRAM_FILE) == @__FILE__
    Ribasim.run()
end

end # module
