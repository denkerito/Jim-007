import {ReactNode, useMemo, useState} from "react";
import {NavLink, useParams} from "react-router-dom";
import {useInfiniteQuery, useQuery} from "@tanstack/react-query";
import {ApiError, historyApi, Workout, WorkoutExercise} from "./api";

const dateFormat = new Intl.DateTimeFormat("en-GB", {day: "numeric", month: "long", year: "numeric"});

function formatDate(value: string) {
  const parts = value.split("-");
  return dateFormat.format(
    new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
  );
}

function formatLoad(value: string, unit: string) {
  return `${Number(value).toLocaleString("en-GB", {maximumFractionDigits: 3})} ${unit}`;
}

export function AppShell({children}: {children: ReactNode}) {
  const links = [["/", "Dashboard"], ["/workouts", "Workouts"], ["/exercises", "Exercises"], ["/account", "Account"]] as const;
  return <main className="min-h-screen"><header className="border-b border-ink/10 bg-white/70 backdrop-blur"><div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-8 gap-y-4 px-6 py-5"><NavLink to="/" className="mr-auto font-black tracking-tight text-xl">JIM<span className="rounded bg-lime px-1">007</span></NavLink><nav aria-label="Main navigation" className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">{links.map(([to,label])=><NavLink key={to} to={to} end={to==="/"} className={({isActive})=>`rounded-xl px-3 py-2 text-sm font-bold ${isActive?"bg-ink text-white":"text-moss hover:bg-cream hover:text-ink"}`}>{label}</NavLink>)}</nav><span className="hidden text-sm text-moss lg:block">Train. Track. Improve.</span></div></header>{children}</main>;
}

function PageHeader({eyebrow, title, description}: {eyebrow: string; title: string; description: string}) {
  return <div><p className="eyebrow">{eyebrow}</p><h1 className="mt-2 text-4xl font-extrabold sm:text-5xl">{title}</h1><p className="mt-3 max-w-2xl text-moss">{description}</p></div>;
}

function QueryError({error, retry}: {error: unknown; retry: () => void}) {
  const message = error instanceof ApiError ? error.message : "We could not load this page.";
  return <div role="alert" className="card"><h2 className="text-xl font-bold">Something went wrong</h2><p className="mt-2 text-moss">{message}</p><button className="button-secondary mt-5" onClick={retry}>Try again</button></div>;
}

function LoadingCards() {return <div aria-label="Loading" className="grid gap-4"><div className="card h-36 animate-pulse bg-white/70"/><div className="card h-36 animate-pulse bg-white/70"/></div>}

function Sets({occurrence}: {occurrence: WorkoutExercise}) {
  return <div className="mt-3 overflow-hidden rounded-2xl border border-ink/10"><div className="grid grid-cols-[3rem_1fr_1fr] bg-cream px-4 py-2 text-xs font-bold uppercase tracking-wider text-moss"><span>Set</span><span>Load</span><span>Reps</span></div>{occurrence.sets.map(set=><div key={set.id} className="grid grid-cols-[3rem_1fr_1fr] border-t border-ink/10 px-4 py-3 text-sm"><span className="text-moss">{set.set_number}</span><span>{set.load?formatLoad(set.load.value,set.load.unit):"Bodyweight"}</span><span>{set.repetitions}</span>{set.notes&&<p className="col-span-3 mt-2 text-xs text-moss">{set.notes}</p>}</div>)}</div>;
}

export function WorkoutCard({workout, defaultOpen=false}: {workout: Workout; defaultOpen?: boolean}) {
  const [open,setOpen]=useState(defaultOpen);
  return <article className="card"><button className="w-full text-left" aria-expanded={open} onClick={()=>setOpen(value=>!value)}><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">{formatDate(workout.performed_on)}</p><h2 className="mt-2 text-2xl font-bold">{workout.program_workout?.alias||"Workout"}</h2></div><span className="rounded-full bg-cream px-3 py-1 text-sm font-bold">{workout.exercises.length} exercise{workout.exercises.length===1?"":"s"}</span></div>{workout.notes&&<p className="mt-3 text-sm text-moss">{workout.notes}</p>}<p className="mt-4 text-sm text-moss">{workout.exercises.map(item=>item.exercise.name).join(" · ")}</p><span className="mt-4 inline-block text-sm font-bold underline">{open?"Hide details":"View details"}</span></button>{open&&<div className="mt-6 space-y-6 border-t border-ink/10 pt-6">{workout.exercises.map(item=><section key={item.id}><div className="flex flex-wrap items-center justify-between gap-2"><NavLink className="text-lg font-bold underline decoration-lime decoration-4 underline-offset-4" to={`/exercises/${item.exercise.id}`}>{item.exercise.name}</NavLink><span className="text-sm text-moss">{item.sets.length} set{item.sets.length===1?"":"s"}</span></div>{item.notes&&<p className="mt-2 text-sm text-moss">{item.notes}</p>}<Sets occurrence={item}/></section>)}</div>}</article>;
}

export function DashboardPage() {
  const query=useQuery({queryKey:["workouts","recent",3],queryFn:()=>historyApi.workouts(3)});
  return <section className="mx-auto max-w-6xl px-6 py-10"><PageHeader eyebrow="Overview" title="Dashboard" description="Your latest training activity at a glance."/><div className="mt-10">{query.isLoading?<LoadingCards/>:query.error?<QueryError error={query.error} retry={()=>void query.refetch()}/>:query.data?.items.length===0?<div className="card text-center"><h2 className="text-2xl font-bold">No completed workouts yet</h2><p className="mt-2 text-moss">Completed workouts will appear here.</p><NavLink className="button-secondary mt-6" to="/workouts">Open workout history</NavLink></div>:<><div className="mb-5 flex items-end justify-between"><div><p className="eyebrow">Recent activity</p><h2 className="mt-1 text-2xl font-bold">Latest workouts</h2></div><NavLink className="text-sm font-bold underline" to="/workouts">View all workouts</NavLink></div><div className="grid gap-5">{query.data?.items.map((workout,index)=><WorkoutCard key={workout.id} workout={workout} defaultOpen={index===0}/>)}</div></>}</div></section>;
}

export function WorkoutsPage() {
  const query=useInfiniteQuery({queryKey:["workouts",10],queryFn:({pageParam})=>historyApi.workouts(10,pageParam),initialPageParam:undefined as string|undefined,getNextPageParam:last=>last.next_cursor??undefined});
  const items=query.data?.pages.flatMap(page=>page.items)??[];
  return <section className="mx-auto max-w-4xl px-6 py-10"><PageHeader eyebrow="Training log" title="Workouts" description="Browse every completed workout and expand it to inspect exercises and sets."/><div className="mt-10">{query.isLoading?<LoadingCards/>:query.error?<QueryError error={query.error} retry={()=>void query.refetch()}/>:items.length===0?<div className="card text-center"><h2 className="text-2xl font-bold">No workouts found</h2><p className="mt-2 text-moss">Your completed workouts will appear here.</p></div>:<div className="grid gap-5">{items.map(workout=><WorkoutCard key={workout.id} workout={workout}/>)}{query.hasNextPage&&<button className="button-secondary mx-auto mt-2" disabled={query.isFetchingNextPage} onClick={()=>void query.fetchNextPage()}>{query.isFetchingNextPage?"Loading…":"Load more"}</button>}</div>}</div></section>;
}

export function ExercisesPage() {
  const [search,setSearch]=useState(""); const query=useQuery({queryKey:["exercises"],queryFn:historyApi.exercises});
  const items=useMemo(()=>{const term=search.trim().toLocaleLowerCase();return (query.data?.items??[]).filter(item=>item.name.toLocaleLowerCase().includes(term))},[query.data,search]);
  return <section className="mx-auto max-w-5xl px-6 py-10"><PageHeader eyebrow="Exercise library" title="Exercises" description="Find an exercise and review its complete training history."/><div className="mt-8"><label className="block"><span className="label">Search exercises</span><input type="search" placeholder="e.g. Bench Press" value={search} onChange={event=>setSearch(event.target.value)}/></label></div><div className="mt-8">{query.isLoading?<LoadingCards/>:query.error?<QueryError error={query.error} retry={()=>void query.refetch()}/>:query.data?.items.length===0?<div className="card text-center"><h2 className="text-2xl font-bold">No exercises yet</h2><p className="mt-2 text-moss">Exercises will appear after they are added to your training log.</p></div>:items.length===0?<p className="card text-center text-moss">No exercises match “{search}”.</p>:<div className="grid gap-3 sm:grid-cols-2">{items.map(item=><NavLink key={item.id} to={`/exercises/${item.id}`} className="card group flex items-center justify-between"><span className="font-bold group-hover:underline">{item.name}</span><span aria-hidden="true">→</span></NavLink>)}</div>}</div></section>;
}

export function ExerciseHistoryPage() {
  const {exerciseId=""}=useParams(); const query=useInfiniteQuery({queryKey:["exercise-history",exerciseId,10],queryFn:({pageParam})=>historyApi.exerciseHistory(exerciseId,10,pageParam),initialPageParam:undefined as string|undefined,getNextPageParam:last=>last.next_cursor??undefined,enabled:!!exerciseId});
  const items=query.data?.pages.flatMap(page=>page.items)??[]; const exercise=query.data?.pages[0]?.exercise;
  return <section className="mx-auto max-w-4xl px-6 py-10">{query.isLoading?<LoadingCards/>:query.error?<><PageHeader eyebrow="Exercise history" title="Exercise" description="Review every completed performance."/><div className="mt-10"><QueryError error={query.error} retry={()=>void query.refetch()}/></div></>:<><PageHeader eyebrow="Exercise history" title={exercise?.name||"Exercise"} description="Every completed workout containing this exercise."/><div className="mt-10">{items.length===0?<div className="card text-center"><h2 className="text-2xl font-bold">No completed performances</h2><p className="mt-2 text-moss">This exercise has no completed workout history yet.</p></div>:<div className="grid gap-5">{items.map(item=><article key={item.workout_id} className="card"><p className="eyebrow">{formatDate(item.performed_on)}</p>{item.workout_notes&&<p className="mt-2 text-sm text-moss">{item.workout_notes}</p>}<div className="mt-5 space-y-5">{item.occurrences.map(occurrence=><section key={occurrence.id}>{occurrence.notes&&<p className="text-sm text-moss">{occurrence.notes}</p>}<Sets occurrence={occurrence}/></section>)}</div></article>)}{query.hasNextPage&&<button className="button-secondary mx-auto mt-2" disabled={query.isFetchingNextPage} onClick={()=>void query.fetchNextPage()}>{query.isFetchingNextPage?"Loading…":"Load more"}</button>}</div>}</div></>}</section>;
}
