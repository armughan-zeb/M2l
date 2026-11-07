"""Bounded fresh-media repair for actual repeated pixels, before export."""
from concurrent.futures import ThreadPoolExecutor, wait
from .job_context import JobThreadPoolExecutor as ThreadPoolExecutor
from threading import Lock
import time


def recover_repeated_media(media_items, scene_texts, audit, retrieve, verify, *, budget_seconds=70, cancellation=None):
    from .perceptual_media import PixelSampler, samples_match

    indices=sorted({int(row['scene_index']) for row in audit.get('unresolved_repeats') or []})
    indices=[i for i in indices if 0 <= i < len(media_items)]
    deadline=time.monotonic()+max(.01,min(120,budget_seconds))
    # Cache actual selected pixels across the film. A changed URL, filename,
    # or source timestamp does not make a duplicate into a fresh shot.
    reference_sampler=PixelSampler(min(20.0,max(.1,budget_seconds*.25)))
    reference_samples=[]
    for scene_index,item in enumerate(media_items):
        if not isinstance(item,dict):
            continue
        selected=item.get('shots') if isinstance(item.get('shots'),list) and item.get('shots') else [item]
        for shot in selected:
            if not isinstance(shot,dict):
                continue
            samples,_=reference_sampler.sample(shot)
            if samples:
                reference_samples.append((scene_index,samples))
    accepted_samples=[]
    sample_lock=Lock()
    rejected_repeats=[]

    def worker(index):
        if cancellation is not None:cancellation.raise_if_cancelled()
        accepted=[]
        # Retry once when a provider returns a different URL for the same
        # underlying image. The retry stays inside the shared film deadline.
        for attempt in range(2):
            if time.monotonic()>=deadline:break
            item={**(media_items[index] if isinstance(media_items[index],dict) else {}),
                  '_repeat_recovery_attempt':attempt}
            rows=retrieve(index,scene_texts[index],item,deadline)
            for candidate in rows or []:
                if time.monotonic()>=deadline:break
                samples,_=PixelSampler(min(4.0,max(.05,deadline-time.monotonic()))).sample(candidate)
                if not samples:
                    rejected_repeats.append({'scene_index':index,'candidate_id':candidate.get('candidate_id') or '',
                                             'reason':'candidate_pixels_unavailable'})
                    continue
                with sample_lock:
                    repeats_anywhere=any(samples_match(samples,known,both_video=False)
                                         for _scene,known in reference_samples+accepted_samples)
                if repeats_anywhere:
                    rejected_repeats.append({'scene_index':index,'candidate_id':candidate.get('candidate_id') or '',
                                             'reason':'candidate_repeats_selected_film_pixels'})
                    continue
                verdict=verify(index,candidate)
                if verdict.get('accepted') is True and verdict.get('visual_checked') is True and verdict.get('verified_asset_sha256'):
                    row={**candidate,'visual_decision':verdict,'qwen_result':verdict,'verified':True,
                         'verification_status':verdict.get('verification_status') or verdict.get('status') or 'verified',
                         'asset_fingerprint':verdict['verified_asset_sha256']}
                    with sample_lock:
                        accepted_samples.append((index,samples))
                    accepted.append(row)
            if accepted:
                break
        return index,accepted
    pool=ThreadPoolExecutor(max_workers=min(3,len(indices) or 1),thread_name_prefix='media-quality-repair')
    futures={pool.submit(worker,i):i for i in indices}
    repaired,failures=[],[]
    try:
        pending=set(futures)
        while pending and time.monotonic()<deadline:
            if cancellation is not None:cancellation.raise_if_cancelled()
            done,pending=wait(pending,timeout=min(.25,max(.001,deadline-time.monotonic())))
            for future in done:
                try:
                    index,rows=future.result()
                    if rows:
                        media_items[index]['alternates']=rows+list(media_items[index].get('alternates') or [])
                        repaired.append(index)
                    else:failures.append({'scene_index':futures[future],'reason':'No newly verified distinct candidates'})
                except Exception as exc:
                    failures.append({'scene_index':futures[future],
                                     'reason':f'{type(exc).__name__}:{str(exc)[:240]}'})
        for future in pending:
            future.cancel()
            failures.append({'scene_index':futures[future],'reason':'Recovery deadline exceeded'})
    finally:
        pool.shutdown(wait=False,cancel_futures=True)
    return {'requested_scene_indices':indices,'new_verified_candidate_scene_indices':repaired,'failures':failures,
            'rejected_duplicate_candidate_count':len(rejected_repeats),'rejected_duplicate_candidates':rejected_repeats[:40],
            'acceptance':'requires_repeat_check_again'}
