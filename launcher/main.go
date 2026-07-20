package main

import (
	"fmt"
	"os"

	"github.com/turbo3000r/DiscordCombatAI/launcher/cmd"
)

func main() {
	if err := cmd.Execute(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "launcher:", err)
		os.Exit(1)
	}
}
